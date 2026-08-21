from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib import resources
from pathlib import Path
from typing import Any, cast

from jayspray.identity import components_compatible, identity_for, normalize_observation
from jayspray.models import FirmwareObservation, ReleaseState, validate_transition


def _now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(frozen=True, slots=True)
class MergeResult:
    release_id: str
    outcome: str
    source_count: int


class Database:
    def __init__(self, path: Path | str = ":memory:") -> None:
        self.path = path
        if path != ":memory:":
            db_path = Path(path)
            db_path.parent.mkdir(mode=0o750, parents=True, exist_ok=True)
        self.connection = sqlite3.connect(str(path), timeout=30.0)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.execute("PRAGMA busy_timeout = 30000")
        if path != ":memory:":
            self.connection.execute("PRAGMA journal_mode = WAL")

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> Database:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        if self.connection.in_transaction:
            yield self.connection
            return
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            yield self.connection
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise

    def migrate(self) -> None:
        self.connection.execute(
            "CREATE TABLE IF NOT EXISTS schema_migration "
            "(version TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
        )
        package = resources.files("jayspray").joinpath("migration_sql")
        migrations = sorted(
            (item for item in package.iterdir() if item.name.endswith(".sql")),
            key=lambda item: item.name,
        )
        for migration in migrations:
            exists = self.connection.execute(
                "SELECT 1 FROM schema_migration WHERE version = ?", (migration.name,)
            ).fetchone()
            if exists:
                continue
            sql = migration.read_text(encoding="utf-8")
            with self.transaction():
                statement = ""
                for line in sql.splitlines(keepends=True):
                    statement += line
                    if sqlite3.complete_statement(statement):
                        self.connection.execute(statement)
                        statement = ""
                if statement.strip():
                    raise sqlite3.OperationalError(
                        f"incomplete SQL statement in migration {migration.name}"
                    )
                self.connection.execute(
                    "INSERT INTO schema_migration(version, applied_at) VALUES (?, ?)",
                    (migration.name, _now()),
                )

    def clone_in_memory(self) -> Database:
        clone = Database()
        self.connection.backup(clone.connection)
        return clone

    @classmethod
    def readonly_snapshot(cls, path: Path) -> Database:
        snapshot = cls()
        if not path.exists():
            snapshot.migrate()
            return snapshot
        source = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=30.0)
        try:
            source.backup(snapshot.connection)
        finally:
            source.close()
        return snapshot

    def start_run(self, command: str, *, dry_run: bool) -> str:
        run_id = str(uuid.uuid4())
        self.connection.execute(
            "INSERT INTO run(id, command, dry_run, started_at, status) VALUES (?, ?, ?, ?, ?)",
            (run_id, command, int(dry_run), _now(), "RUNNING"),
        )
        self.connection.commit()
        return run_id

    def finish_run(self, run_id: str, status: str, metrics: dict[str, Any]) -> None:
        if status not in {"SUCCESS", "PARTIAL", "FAILED"}:
            raise ValueError("invalid run completion status")
        self.connection.execute(
            "UPDATE run SET finished_at = ?, status = ?, metrics_json = ? WHERE id = ?",
            (_now(), status, json.dumps(metrics, sort_keys=True), run_id),
        )
        self.connection.commit()

    def record_failure(
        self,
        *,
        operation: str,
        message: str,
        classification: str = "RETRYABLE",
        run_id: str | None = None,
        release_id: str | None = None,
        source: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        if classification not in {"RETRYABLE", "PERMANENT"}:
            raise ValueError("invalid failure classification")
        from jayspray.logging import redact, redact_text

        safe_message = redact_text(message)[:2000]
        safe_details = json.dumps(redact(details or {}), sort_keys=True)[:8000]
        now = _now()
        self.connection.execute(
            """INSERT INTO failure(
                run_id, firmware_release_id, source, operation, classification, message,
                details_json, first_failed_at, last_failed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                run_id,
                release_id,
                source,
                operation,
                classification,
                safe_message,
                safe_details,
                now,
                now,
            ),
        )
        self.connection.commit()

    def _release_candidates(self, weak_key: str) -> list[sqlite3.Row]:
        return list(
            self.connection.execute(
                "SELECT * FROM firmware_release WHERE weak_key = ? ORDER BY created_at", (weak_key,)
            )
        )

    def upsert_observation(self, observation: FirmwareObservation) -> MergeResult:
        item = normalize_observation(observation)
        ident = identity_for(item)
        observed = item.observed_at.isoformat()
        payload = json.dumps(item.as_json_dict(), sort_keys=True, separators=(",", ":"))

        with self.transaction():
            previous = self.connection.execute(
                "SELECT firmware_release_id FROM source_observation "
                "WHERE source = ? AND source_record_key = ?",
                (item.source, item.source_record_key),
            ).fetchone()
            if previous:
                release_id = str(previous["firmware_release_id"])
                self._merge_release(release_id, item, ident.strong_key, observed)
                self.connection.execute(
                    """UPDATE source_observation
                       SET sales_csc = ?, ap_version = ?, full_version = ?,
                           source_url = ?, detail_url = ?, payload_json = ?, last_seen_at = ?,
                           observation_count = observation_count + 1
                       WHERE source = ? AND source_record_key = ?""",
                    (
                        item.sales_csc,
                        item.ap_version,
                        item.full_version or item.ap_version,
                        item.source_url,
                        item.detail_url,
                        payload,
                        observed,
                        item.source,
                        item.source_record_key,
                    ),
                )
                outcome = "matched_observation"
            else:
                release = None
                if ident.strong_key:
                    release = self.connection.execute(
                        "SELECT * FROM firmware_release WHERE strong_key = ?", (ident.strong_key,)
                    ).fetchone()
                if release is None:
                    compatible = [
                        row
                        for row in self._release_candidates(ident.weak_key)
                        if components_compatible(dict(row), item)
                        and (row["strong_key"] is None or row["strong_key"] == ident.strong_key)
                    ]
                    if len(compatible) == 1:
                        release = compatible[0]
                if release is None:
                    release_id = str(uuid.uuid4())
                    self._insert_release(
                        release_id, item, ident.weak_key, ident.strong_key, observed
                    )
                    outcome = "new_release"
                else:
                    release_id = str(release["id"])
                    self._merge_release(release_id, item, ident.strong_key, observed)
                    outcome = "merged_source"
                self.connection.execute(
                    """INSERT INTO source_observation(
                        firmware_release_id, source, source_record_key, sales_csc, ap_version,
                        full_version, source_url, detail_url, payload_json,
                        first_seen_at, last_seen_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        release_id,
                        item.source,
                        item.source_record_key,
                        item.sales_csc,
                        item.ap_version,
                        item.full_version or item.ap_version,
                        item.source_url,
                        item.detail_url,
                        payload,
                        observed,
                        observed,
                    ),
                )

            count = int(
                self.connection.execute(
                    "SELECT count(*) FROM source_observation WHERE firmware_release_id = ?",
                    (release_id,),
                ).fetchone()[0]
            )
        return MergeResult(release_id=release_id, outcome=outcome, source_count=count)

    def _insert_release(
        self,
        release_id: str,
        item: FirmwareObservation,
        weak_key: str,
        strong_key: str | None,
        observed: str,
    ) -> None:
        self.connection.execute(
            """INSERT INTO firmware_release(
                id, weak_key, strong_key, model, sales_csc, device_name, country, region,
                carrier, ap_version, csc_version, cp_version, data_version, full_version,
                android_version, one_ui_version, security_patch, bootloader_revision,
                changelist, build_date, source_upload_date, source_updated_date, expected_size,
                first_discovered_at, last_observed_at, state_updated_at, created_at, updated_at
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?
            )""",
            (
                release_id,
                weak_key,
                strong_key,
                item.model,
                item.sales_csc,
                item.device_name,
                item.country,
                item.region,
                item.carrier,
                item.ap_version,
                item.csc_version,
                item.cp_version,
                item.data_version,
                item.full_version,
                item.android_version,
                item.one_ui_version,
                item.security_patch,
                item.bootloader_revision,
                item.changelist,
                item.build_date,
                item.source_upload_date,
                item.source_updated_date,
                item.expected_size,
                observed,
                observed,
                observed,
                observed,
                observed,
            ),
        )

    def _merge_release(
        self,
        release_id: str,
        item: FirmwareObservation,
        strong_key: str | None,
        observed: str,
    ) -> None:
        row = self.get_release(release_id)
        if row is None:
            raise KeyError(release_id)
        fields = (
            "device_name",
            "country",
            "region",
            "carrier",
            "csc_version",
            "cp_version",
            "data_version",
            "full_version",
            "android_version",
            "one_ui_version",
            "security_patch",
            "bootloader_revision",
            "changelist",
            "build_date",
            "source_upload_date",
            "source_updated_date",
            "expected_size",
        )
        values = {field: row[field] or getattr(item, field) for field in fields}
        merged_strong = row["strong_key"] or strong_key
        assignments = ", ".join(f"{field} = ?" for field in fields)
        self.connection.execute(
            f"UPDATE firmware_release SET {assignments}, strong_key = ?, "  # noqa: S608
            "last_observed_at = ?, updated_at = ? WHERE id = ?",
            (*values.values(), merged_strong, observed, observed, release_id),
        )

    def get_release(self, release_id: str) -> sqlite3.Row | None:
        return cast(
            sqlite3.Row | None,
            self.connection.execute(
                "SELECT * FROM firmware_release WHERE id = ?", (release_id,)
            ).fetchone(),
        )

    def set_state(self, release_id: str, target: ReleaseState) -> None:
        with self.transaction():
            row = self.get_release(release_id)
            if row is None:
                raise KeyError(release_id)
            validate_transition(ReleaseState(row["state"]), target)
            self.connection.execute(
                "UPDATE firmware_release SET state = ?, state_updated_at = ?, updated_at = ? "
                "WHERE id = ?",
                (target.value, _now(), _now(), release_id),
            )

    def set_resolved_version(
        self, release_id: str, full_version: str, *, sales_csc: str | None = None
    ) -> None:
        parts = full_version.split("/")
        if len(parts) < 3:
            raise ValueError("resolved firmware version needs at least AP/CSC/CP")
        with self.transaction():
            row = self.get_release(release_id)
            if row is None:
                raise KeyError(release_id)
            if row["ap_version"] != parts[0]:
                raise ValueError("resolved firmware AP does not match canonical release")
            data_version = parts[3] if len(parts) > 3 else row["data_version"]
            self.connection.execute(
                """UPDATE firmware_release
                   SET full_version = ?, csc_version = ?, cp_version = ?, data_version = ?,
                       sales_csc = COALESCE(?, sales_csc), updated_at = ?
                   WHERE id = ?""",
                (
                    full_version,
                    parts[1],
                    parts[2],
                    data_version,
                    sales_csc,
                    _now(),
                    release_id,
                ),
            )
            current = ReleaseState(row["state"])
            if current == ReleaseState.DISCOVERED:
                validate_transition(current, ReleaseState.RESOLVED)
                self.connection.execute(
                    "UPDATE firmware_release SET state = 'RESOLVED', "
                    "state_updated_at = ? WHERE id = ?",
                    (_now(), release_id),
                )

    def queue(self, release_id: str) -> None:
        row = self.get_release(release_id)
        if row is None:
            raise KeyError(release_id)
        current = ReleaseState(row["state"])
        if current not in {ReleaseState.QUEUED, ReleaseState.FAILED}:
            self.set_state(release_id, ReleaseState.QUEUED)
        now = _now()
        self.connection.execute(
            """INSERT INTO download_job(firmware_release_id, status, created_at, updated_at)
               VALUES (?, 'QUEUED', ?, ?)
               ON CONFLICT(firmware_release_id) DO UPDATE SET
                 status = 'QUEUED', updated_at = excluded.updated_at""",
            (release_id, now, now),
        )
        self.connection.commit()

    def list_releases(
        self, *, limit: int = 100, states: Iterable[str] | None = None
    ) -> list[sqlite3.Row]:
        if limit < 1 or limit > 10000:
            raise ValueError("limit must be between 1 and 10000")
        state_values = tuple(states or ())
        if state_values:
            placeholders = ",".join("?" for _ in state_values)
            return list(
                self.connection.execute(
                    f"SELECT * FROM firmware_release WHERE state IN ({placeholders}) "  # noqa: S608
                    "ORDER BY first_discovered_at DESC LIMIT ?",
                    (*state_values, limit),
                )
            )
        return list(
            self.connection.execute(
                "SELECT * FROM firmware_release ORDER BY first_discovered_at DESC LIMIT ?", (limit,)
            )
        )

    def sources_for_release(self, release_id: str) -> list[str]:
        return [
            str(row[0])
            for row in self.connection.execute(
                "SELECT DISTINCT source FROM source_observation "
                "WHERE firmware_release_id = ? ORDER BY source",
                (release_id,),
            )
        ]

    def route_observations(self, release_id: str) -> list[dict[str, Any]]:
        observations: list[dict[str, Any]] = []
        for row in self.connection.execute(
            """SELECT source, sales_csc, full_version, first_seen_at
               FROM source_observation WHERE firmware_release_id = ? """
            "ORDER BY first_seen_at",
            (release_id,),
        ):
            observations.append(
                {
                    "source": row["source"],
                    "csc": row["sales_csc"],
                    "full_version": row["full_version"],
                    "first_observed": row["first_seen_at"],
                }
            )
        return observations

    def search(
        self,
        query: str | None = None,
        *,
        csc: str | None = None,
        pda: str | None = None,
        limit: int = 100,
    ) -> list[sqlite3.Row]:
        if limit < 1 or limit > 10000:
            raise ValueError("limit must be between 1 and 10000")
        escaped_query = (
            query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") if query else None
        )
        model_pattern = f"%{escaped_query.upper()}%" if escaped_query else None
        name_pattern = f"%{escaped_query}%" if escaped_query else None
        escaped_pda = (
            pda.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") if pda else None
        )
        pda_pattern = f"{escaped_pda.upper()}%" if escaped_pda else None
        normalized_csc = csc.upper() if csc else None
        return list(
            self.connection.execute(
                """SELECT * FROM firmware_release
                   WHERE (? IS NULL OR model LIKE ? ESCAPE '\\'
                                      OR device_name LIKE ? ESCAPE '\\')
                     AND (? IS NULL OR sales_csc = ?)
                     AND (? IS NULL OR ap_version LIKE ? ESCAPE '\\')
                   ORDER BY first_discovered_at DESC LIMIT ?""",
                (
                    escaped_query,
                    model_pattern,
                    name_pattern,
                    normalized_csc,
                    normalized_csc,
                    escaped_pda,
                    pda_pattern,
                    limit,
                ),
            )
        )

    def record_artifact(
        self,
        *,
        release_id: str,
        kind: str,
        path: Path,
        size: int,
        sha256: str,
        status: str,
        md5: str | None = None,
        crc32: str | None = None,
    ) -> None:
        self.connection.execute(
            """INSERT INTO artifact(
                firmware_release_id, kind, path, size, sha256, crc32, md5, status, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(firmware_release_id, kind, path) DO UPDATE SET
                size = excluded.size, sha256 = excluded.sha256, crc32 = excluded.crc32,
                md5 = excluded.md5, status = excluded.status""",
            (release_id, kind, str(path), size, sha256, crc32, md5, status, _now()),
        )
        self.connection.commit()

    def find_binary_blob(self, sha256: str) -> sqlite3.Row | None:
        return cast(
            sqlite3.Row | None,
            self.connection.execute(
                "SELECT * FROM binary_blob WHERE sha256 = ?", (sha256,)
            ).fetchone(),
        )

    def record_binary_blob(self, sha256: str, path: Path, size: int) -> None:
        self.connection.execute(
            "INSERT OR IGNORE INTO binary_blob(sha256, path, size, created_at) VALUES (?, ?, ?, ?)",
            (sha256, str(path), size, _now()),
        )
        self.connection.commit()

    def update_watch_target(
        self,
        model: str,
        sales_csc: str,
        *,
        enabled: bool,
        successful: bool,
        last_version: str | None = None,
        error: str | None = None,
    ) -> None:
        from jayspray.logging import redact_text

        now = _now()
        safe_error = redact_text(error)[:2000] if error else None
        self.connection.execute(
            """INSERT INTO watch_target(
                 model, sales_csc, enabled, last_checked_at, last_success_at,
                 last_version, last_error, created_at, updated_at
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(model, sales_csc) DO UPDATE SET
                 enabled = excluded.enabled,
                 last_checked_at = excluded.last_checked_at,
                 last_success_at = CASE WHEN ? THEN excluded.last_success_at
                                        ELSE watch_target.last_success_at END,
                 last_version = COALESCE(excluded.last_version, watch_target.last_version),
                 last_error = excluded.last_error,
                 updated_at = excluded.updated_at""",
            (
                model,
                sales_csc,
                int(enabled),
                now,
                now if successful else None,
                last_version,
                safe_error,
                now,
                now,
                int(successful),
            ),
        )
        self.connection.commit()

    def update_source_checkpoint(
        self,
        source: str,
        *,
        successful: bool,
        latest_record_key: str | None = None,
        error: str | None = None,
    ) -> None:
        from jayspray.logging import redact_text

        now = _now()
        safe_error = redact_text(error)[:2000] if error else None
        self.connection.execute(
            """INSERT INTO source_checkpoint(
                 source, last_checked_at, last_success_at, latest_record_key,
                 last_error, updated_at
               ) VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(source) DO UPDATE SET
                 last_checked_at = excluded.last_checked_at,
                 last_success_at = CASE WHEN ? THEN excluded.last_success_at
                                        ELSE source_checkpoint.last_success_at END,
                 latest_record_key = COALESCE(
                     excluded.latest_record_key, source_checkpoint.latest_record_key
                 ),
                 last_error = excluded.last_error,
                 updated_at = excluded.updated_at""",
            (
                source,
                now,
                now if successful else None,
                latest_record_key,
                safe_error,
                now,
                int(successful),
            ),
        )
        self.connection.commit()

    def status_summary(self) -> dict[str, Any]:
        counts = {
            str(row["state"]): int(row["count"])
            for row in self.connection.execute(
                "SELECT state, count(*) AS count FROM firmware_release GROUP BY state"
            )
        }
        targets = [
            dict(row)
            for row in self.connection.execute(
                """SELECT model, sales_csc, enabled, last_checked_at, last_success_at,
                          last_version, last_error
                   FROM watch_target ORDER BY model, sales_csc"""
            )
        ]
        sources = [
            dict(row)
            for row in self.connection.execute(
                """SELECT source, last_checked_at, last_success_at,
                          latest_record_key, last_error
                   FROM source_checkpoint ORDER BY source"""
            )
        ]
        last_run = self.connection.execute(
            "SELECT * FROM run ORDER BY started_at DESC LIMIT 1"
        ).fetchone()
        failures = int(
            self.connection.execute(
                "SELECT count(*) FROM failure WHERE resolved_at IS NULL"
            ).fetchone()[0]
        )
        return {
            "states": counts,
            "sources": sources,
            "targets": targets,
            "last_run": dict(last_run) if last_run else None,
            "unresolved_failures": failures,
        }
