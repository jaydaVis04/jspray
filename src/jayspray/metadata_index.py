from __future__ import annotations

import fcntl
import json
import os
import re
import stat as stat_module
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from jayspray.db import Database
from jayspray.identity import normalized_csc, normalized_model, normalized_version

MODEL_BYTES_RE = re.compile(rb"(?i)(?<![A-Z0-9])SM-?[A-Z0-9]{3,16}(?![A-Z0-9])")
SCAN_BATCH = 10_000
MAX_LAST_LINE_BYTES = 1024 * 1024


@dataclass(frozen=True, slots=True)
class CacheStats:
    rebuilt: bool
    bytes_scanned: int
    models_added: int


class ExternalMetadataIndex:
    """Incremental SQLite model index for a large line-oriented metadata file."""

    def __init__(self, database: Database, path: Path) -> None:
        if not path.is_absolute():
            raise ValueError("external metadata path must be absolute")
        self.database = database
        self.path = path
        self.path_key = str(path)

    def refresh(self) -> CacheStats:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(self.path, flags)
        try:
            stat = os.fstat(descriptor)
            if not stat_module.S_ISREG(stat.st_mode):
                raise OSError("external metadata path must be a regular file")
            state = self.database.connection.execute(
                "SELECT * FROM external_metadata_state WHERE path = ?", (self.path_key,)
            ).fetchone()
            rebuild = bool(
                state is None
                or int(state["device"]) != stat.st_dev
                or int(state["inode"]) != stat.st_ino
                or stat.st_size < int(state["indexed_offset"])
                or (
                    stat.st_size == int(state["indexed_offset"])
                    and stat.st_mtime_ns != int(state["mtime_ns"])
                )
            )
            start = 0 if rebuild or state is None else int(state["indexed_offset"])
            if not rebuild and start == stat.st_size:
                return CacheStats(False, 0, 0)
            with self.database.transaction():
                if rebuild:
                    self.database.connection.execute(
                        "DELETE FROM external_metadata_model WHERE path = ?", (self.path_key,)
                    )
                os.lseek(descriptor, start, os.SEEK_SET)
                added = 0
                batch: list[tuple[str, str, int]] = []
                with os.fdopen(descriptor, "rb", closefd=False) as handle:
                    while line := handle.readline():
                        line_offset = handle.tell() - len(line)
                        models = {
                            normalized_model(match.group().decode("ascii"))
                            for match in MODEL_BYTES_RE.finditer(line)
                        }
                        batch.extend((self.path_key, model, line_offset) for model in models)
                        if len(batch) >= SCAN_BATCH:
                            before = self.database.connection.total_changes
                            self.database.connection.executemany(
                                "INSERT OR IGNORE INTO external_metadata_model"
                                "(path, model, first_seen_offset) VALUES (?, ?, ?)",
                                batch,
                            )
                            added += self.database.connection.total_changes - before
                            batch.clear()
                    indexed_offset = handle.tell()
                if batch:
                    before = self.database.connection.total_changes
                    self.database.connection.executemany(
                        "INSERT OR IGNORE INTO external_metadata_model"
                        "(path, model, first_seen_offset) VALUES (?, ?, ?)",
                        batch,
                    )
                    added += self.database.connection.total_changes - before
                final_stat = os.fstat(descriptor)
                self.database.connection.execute(
                    """INSERT INTO external_metadata_state(
                         path, device, inode, size, mtime_ns, indexed_offset, updated_at
                       ) VALUES (?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(path) DO UPDATE SET
                         device = excluded.device, inode = excluded.inode,
                         size = excluded.size, mtime_ns = excluded.mtime_ns,
                         indexed_offset = excluded.indexed_offset,
                         updated_at = excluded.updated_at""",
                    (
                        self.path_key,
                        stat.st_dev,
                        stat.st_ino,
                        final_stat.st_size,
                        final_stat.st_mtime_ns,
                        indexed_offset,
                        datetime.now(UTC).isoformat(),
                    ),
                )
            return CacheStats(rebuild, indexed_offset - start, added)
        finally:
            os.close(descriptor)

    def contains(self, model: str) -> bool:
        normalized = normalized_model(model)
        return (
            self.database.connection.execute(
                "SELECT 1 FROM external_metadata_model WHERE path = ? AND model = ?",
                (self.path_key, normalized),
            ).fetchone()
            is not None
        )

    def append_completed(self, record: dict[str, Any]) -> None:
        model = normalized_model(str(record["model"]))
        region = normalized_csc(str(record["region"]))
        full_version = normalized_version(str(record["full_version"]))
        if full_version is None:
            raise ValueError("completed metadata record requires a firmware version")
        safe_record = {
            "model": model,
            "region": region,
            "full_version": full_version,
            "firmware_release_id": str(record["firmware_release_id"]),
            "artifact": str(record["artifact"]),
            "sha256": str(record["sha256"]),
            "completed_at": str(record["completed_at"]),
            "source": "jayspray",
        }
        self.path.parent.mkdir(mode=0o750, parents=True, exist_ok=True)
        flags = os.O_RDWR | os.O_CREAT | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(self.path, flags, 0o640)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            stat = os.fstat(descriptor)
            separator = b""
            if stat.st_size:
                first = os.pread(descriptor, min(stat.st_size, 4096), 0).lstrip()
                if first.startswith(b"["):
                    raise ValueError(
                        "external metadata must be JSON Lines; top-level JSON arrays are read-only"
                    )
                tail_size = min(stat.st_size, MAX_LAST_LINE_BYTES)
                tail = os.pread(descriptor, tail_size, stat.st_size - tail_size)
                lines = [line for line in tail.splitlines() if line.strip()]
                if not lines:
                    separator = b"\n" if not tail.endswith(b"\n") else b""
                else:
                    try:
                        last = json.loads(lines[-1])
                    except json.JSONDecodeError as exc:
                        raise ValueError(
                            "external metadata must contain one complete JSON object per line"
                        ) from exc
                    if not isinstance(last, dict):
                        raise ValueError("external metadata JSONL records must be objects")
                    separator = b"" if tail.endswith(b"\n") else b"\n"
            encoded = json.dumps(safe_record, sort_keys=True, separators=(",", ":")).encode()
            os.write(descriptor, separator + encoded + b"\n")
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        self.refresh()
