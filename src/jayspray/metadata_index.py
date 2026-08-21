from __future__ import annotations

import codecs
import contextlib
import fcntl
import json
import os
import re
import secrets
import stat as stat_module
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from jayspray.db import Database
from jayspray.identity import normalized_model

MODEL_BYTES_RE = re.compile(rb"(?i)(?<![A-Z0-9])SM-?[A-Z0-9]{3,16}(?![A-Z0-9])")
SCAN_BATCH = 10_000
COPY_CHUNK = 1024 * 1024
MAX_CATALOG_RECORD_BYTES = 16 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class CacheStats:
    rebuilt: bool
    bytes_scanned: int
    models_added: int


class ExternalMetadataIndex:
    """Incremental SQLite model index for a large line-oriented metadata file."""

    def __init__(
        self, database: Database, path: Path, *, create_if_missing: bool = False
    ) -> None:
        if not path.is_absolute():
            raise ValueError("external metadata path must be absolute")
        self.database = database
        self.path = path
        self.path_key = str(path)
        self.create_if_missing = create_if_missing

    def _ensure_file(self) -> None:
        if self.path.exists():
            return
        if not self.create_if_missing:
            raise FileNotFoundError(self.path)
        self.path.parent.mkdir(mode=0o750, parents=True, exist_ok=True)
        flags = (
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | os.O_CLOEXEC
            | getattr(os, "O_NOFOLLOW", 0)
        )
        try:
            descriptor = os.open(self.path, flags, 0o640)
        except FileExistsError:
            return
        try:
            if os.write(descriptor, b"{}\n") != 3:
                raise OSError("could not initialize external metadata catalog")
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def refresh(self) -> CacheStats:
        self._ensure_file()
        flags = os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0)
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
            if (
                not rebuild
                and state is not None
                and int(state["indexed_offset"]) == stat.st_size
            ):
                return CacheStats(False, 0, 0)
            start = (
                0
                if rebuild or state is None
                else max(0, int(state["indexed_offset"]) - 4096)
            )
            with self.database.transaction():
                if rebuild:
                    self.database.connection.execute(
                        "DELETE FROM external_metadata_model WHERE path = ?", (self.path_key,)
                    )
                os.lseek(descriptor, start, os.SEEK_SET)
                added = 0
                batch: list[tuple[str, str, int]] = []
                offset = start
                overlap = b""
                while chunk := os.read(descriptor, COPY_CHUNK):
                    data = overlap + chunk
                    data_offset = offset - len(overlap)
                    for match in MODEL_BYTES_RE.finditer(data):
                        batch.append(
                            (
                                self.path_key,
                                normalized_model(match.group().decode("ascii")),
                                max(0, data_offset + match.start()),
                            )
                        )
                    if len(batch) >= SCAN_BATCH:
                        before = self.database.connection.total_changes
                        self.database.connection.executemany(
                            "INSERT OR IGNORE INTO external_metadata_model"
                            "(path, model, first_seen_offset) VALUES (?, ?, ?)",
                            batch,
                        )
                        added += self.database.connection.total_changes - before
                        batch.clear()
                    overlap = data[-64:]
                    offset += len(chunk)
                indexed_offset = offset
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

    def append_catalog_entry(self, key: str, record: dict[str, Any]) -> bool:
        """Atomically append one keyed record to a top-level JSON object.

        Returns False when the model is already present anywhere in the catalog.
        """
        model = normalized_model(str(record["model"]))
        if not key or len(key) > 512 or any(ord(char) < 32 for char in key):
            raise ValueError("metadata catalog key is invalid")
        encoded_object = json.dumps(
            {key: record}, indent=4, ensure_ascii=False, allow_nan=False
        ).encode("utf-8")
        lines = encoded_object.splitlines()
        encoded_entry = b"\n".join(lines[1:-1])
        if not encoded_entry:
            raise ValueError("metadata catalog entry is empty")

        self._ensure_file()
        parent_flags = os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_DIRECTORY", 0)
        parent_descriptor = os.open(self.path.parent, parent_flags)
        descriptor = -1
        temporary_descriptor = -1
        temporary_name = f".{self.path.name}.{secrets.token_hex(12)}.partial"
        try:
            flags = os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(self.path.name, flags, dir_fd=parent_descriptor)
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            details = os.fstat(descriptor)
            if not stat_module.S_ISREG(details.st_mode):
                raise OSError("external metadata path must be a regular file")
            if self._descriptor_contains_model(descriptor, model):
                return False
            root_open, root_close, last_content = self._json_object_bounds(
                descriptor, details.st_size
            )
            del root_open

            temporary_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC
            temporary_descriptor = os.open(
                temporary_name, temporary_flags, 0o600, dir_fd=parent_descriptor
            )
            mode = stat_module.S_IMODE(details.st_mode) & 0o660
            os.fchmod(temporary_descriptor, mode or 0o600)
            copy_end = last_content + 1 if last_content is not None else root_close
            self._copy_bytes(descriptor, temporary_descriptor, copy_end)
            if last_content is not None:
                os.write(temporary_descriptor, b",")
            os.write(temporary_descriptor, b"\n" + encoded_entry + b"\n")
            self._copy_bytes(
                descriptor,
                temporary_descriptor,
                details.st_size - root_close,
                source_offset=root_close,
            )
            os.fsync(temporary_descriptor)
            os.close(temporary_descriptor)
            temporary_descriptor = -1
            current = os.stat(
                self.path.name, dir_fd=parent_descriptor, follow_symlinks=False
            )
            if current.st_dev != details.st_dev or current.st_ino != details.st_ino:
                raise OSError("external metadata changed before atomic replacement")
            os.replace(
                temporary_name,
                self.path.name,
                src_dir_fd=parent_descriptor,
                dst_dir_fd=parent_descriptor,
            )
            os.fsync(parent_descriptor)
            replacement = os.open(self.path.name, flags, dir_fd=parent_descriptor)
            try:
                replacement_stat = os.fstat(replacement)
            finally:
                os.close(replacement)
            self._mark_appended(model, replacement_stat)
            return True
        finally:
            if temporary_descriptor >= 0:
                os.close(temporary_descriptor)
            with contextlib.suppress(FileNotFoundError):
                os.unlink(temporary_name, dir_fd=parent_descriptor)
            if descriptor >= 0:
                os.close(descriptor)
            os.close(parent_descriptor)

    @staticmethod
    def _copy_bytes(
        source: int,
        destination: int,
        count: int,
        *,
        source_offset: int = 0,
    ) -> None:
        remaining = count
        offset = source_offset
        while remaining:
            chunk = os.pread(source, min(COPY_CHUNK, remaining), offset)
            if not chunk:
                raise OSError("external metadata changed while it was being copied")
            view = memoryview(chunk)
            while view:
                written = os.write(destination, view)
                if written <= 0:
                    raise OSError("external metadata copy stopped before completion")
                view = view[written:]
            offset += len(chunk)
            remaining -= len(chunk)

    @staticmethod
    def _descriptor_contains_model(descriptor: int, model: str) -> bool:
        offset = 0
        overlap = b""
        while chunk := os.pread(descriptor, COPY_CHUNK, offset):
            data = overlap + chunk
            for match in MODEL_BYTES_RE.finditer(data):
                if normalized_model(match.group().decode("ascii")) == model:
                    return True
            overlap = data[-64:]
            offset += len(chunk)
        return False

    @staticmethod
    def _json_object_bounds(
        descriptor: int, size: int
    ) -> tuple[int, int, int | None]:
        decoder = codecs.getincrementaldecoder("utf-8")("strict")
        stack: list[int] = []
        in_string = False
        escaped = False
        root_open: int | None = None
        root_close: int | None = None
        last_content: int | None = None
        member_start: int | None = None
        members_seen = 0
        position = 0
        while position < size:
            chunk = os.pread(descriptor, min(COPY_CHUNK, size - position), position)
            if not chunk:
                raise ValueError("external metadata changed during validation")
            decoder.decode(chunk, final=False)
            for index, byte in enumerate(chunk):
                absolute = position + index
                if root_close is not None:
                    if byte not in b" \t\r\n":
                        raise ValueError("external metadata has content after its root object")
                    continue
                if in_string:
                    if escaped:
                        escaped = False
                    elif byte == 0x5C:
                        escaped = True
                    elif byte == 0x22:
                        in_string = False
                    if stack:
                        last_content = absolute
                    continue
                if byte in b" \t\r\n":
                    continue
                if root_open is None:
                    if byte != 0x7B:
                        raise ValueError("external metadata root must be a JSON object")
                    root_open = absolute
                    stack.append(byte)
                    member_start = absolute + 1
                    continue
                if byte == 0x22:
                    in_string = True
                    last_content = absolute
                elif byte in (0x7B, 0x5B):
                    stack.append(byte)
                    last_content = absolute
                elif byte in (0x7D, 0x5D):
                    expected = 0x7B if byte == 0x7D else 0x5B
                    if not stack or stack[-1] != expected:
                        raise ValueError("external metadata has unbalanced JSON containers")
                    if len(stack) == 1:
                        if byte != 0x7D:
                            raise ValueError("external metadata root must be a JSON object")
                        if member_start is None:
                            raise ValueError("external metadata member state is invalid")
                        if ExternalMetadataIndex._validate_member(
                            descriptor, member_start, absolute
                        ):
                            members_seen += 1
                        elif members_seen:
                            raise ValueError("external metadata has a trailing comma")
                        root_close = absolute
                        stack.pop()
                    else:
                        stack.pop()
                        last_content = absolute
                else:
                    if not stack:
                        raise ValueError("external metadata root closed unexpectedly")
                    if byte == 0x2C and len(stack) == 1:
                        if member_start is None or not ExternalMetadataIndex._validate_member(
                            descriptor, member_start, absolute
                        ):
                            raise ValueError("external metadata has an empty JSON member")
                        members_seen += 1
                        member_start = absolute + 1
                    last_content = absolute
            position += len(chunk)
        decoder.decode(b"", final=True)
        if root_open is None or root_close is None or stack or in_string or escaped:
            raise ValueError("external metadata contains incomplete JSON")
        if last_content is not None and last_content <= root_open:
            last_content = None
        return root_open, root_close, last_content

    @staticmethod
    def _validate_member(descriptor: int, start: int, end: int) -> bool:
        length = end - start
        if length > MAX_CATALOG_RECORD_BYTES:
            raise ValueError("external metadata record exceeds its size limit")
        fragment = os.pread(descriptor, length, start).strip()
        if not fragment:
            return False
        try:
            parsed = json.loads(b"{" + fragment + b"}")
        except json.JSONDecodeError as exc:
            raise ValueError("external metadata contains an invalid JSON member") from exc
        if not isinstance(parsed, dict) or len(parsed) != 1:
            raise ValueError("external metadata members must be keyed JSON objects")
        return True

    def _mark_appended(self, model: str, details: os.stat_result) -> None:
        with self.database.transaction():
            self.database.connection.execute(
                "INSERT OR IGNORE INTO external_metadata_model"
                "(path, model, first_seen_offset) VALUES (?, ?, ?)",
                (self.path_key, model, max(0, details.st_size - 1)),
            )
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
                    details.st_dev,
                    details.st_ino,
                    details.st_size,
                    details.st_mtime_ns,
                    details.st_size,
                    datetime.now(UTC).isoformat(),
                ),
            )
