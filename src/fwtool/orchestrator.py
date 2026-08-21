from __future__ import annotations

import json
import logging
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fwtool.backend.base import SamsungBackend
from fwtool.config import AppConfig
from fwtool.db import Database
from fwtool.extract import (
    ArchiveError,
    FileManifestEntry,
    extract_firmware,
    sha256_file,
    verify_zip,
)
from fwtool.identity import full_version_components, normalized_csc, normalized_model
from fwtool.logging import log_event
from fwtool.models import FirmwareObservation, ProbeResult, ReleaseState

LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class DiscoveryResult:
    new_releases: int = 0
    matched_observations: int = 0
    candidates: int = 0
    releases: list[dict[str, Any]] = field(default_factory=list)
    target_errors: dict[str, str] = field(default_factory=dict)
    dry_run: bool = False


def _official_observation(model: str, csc: str, full_version: str) -> FirmwareObservation:
    parts = full_version_components(full_version)
    if len(parts) < 3:
        raise ValueError("Samsung history returned a version without AP/CSC/CP components")
    return FirmwareObservation(
        source="samsung_fus",
        source_record_key=f"{model}:{csc}:{full_version}",
        source_url="samsung-fus:SmartHistory",
        detail_url=None,
        model=model,
        sales_csc=csc,
        ap_version=parts[0],
        csc_version=parts[1],
        cp_version=parts[2],
        data_version=parts[3] if len(parts) > 3 else None,
        full_version=full_version,
        download_status="official_samsung_history",
    )


def discover(
    database: Database,
    config: AppConfig,
    backend: SamsungBackend,
    *,
    limit: int | None = None,
    dry_run: bool = False,
    history_limit_per_target: int | None = None,
    command: str = "discover",
) -> DiscoveryResult:
    """Discover official Samsung versions for configured model/CSC targets.

    This function does not contact or parse third-party firmware databases.
    """
    if not any(target.enabled for target in config.targets):
        raise ValueError("no enabled Samsung model/CSC targets are configured")
    target = database.clone_in_memory() if dry_run else database
    result = DiscoveryResult(dry_run=dry_run)
    run_id = target.start_run(command, dry_run=dry_run)
    remaining = limit
    per_target = history_limit_per_target or config.discovery.history_limit_per_target
    try:
        for configured in config.targets:
            if not configured.enabled or remaining == 0:
                continue
            model = normalized_model(configured.model)
            csc = normalized_csc(configured.csc)
            target_key = f"{model}/{csc}"
            try:
                versions = backend.history(model, csc)[:per_target]
                if remaining is not None:
                    versions = versions[:remaining]
                target.update_watch_target(
                    model,
                    csc,
                    enabled=True,
                    successful=True,
                    last_version=versions[0] if versions else None,
                )
                for full_version in versions:
                    observation = _official_observation(model, csc, full_version)
                    merged = target.upsert_observation(observation)
                    result.candidates += 1
                    if merged.outcome == "new_release":
                        result.new_releases += 1
                    else:
                        result.matched_observations += 1
                    result.releases.append(
                        {
                            "id": merged.release_id,
                            "model": model,
                            "csc": csc,
                            "version": full_version,
                            "outcome": merged.outcome,
                        }
                    )
                if remaining is not None:
                    remaining -= len(versions)
            except Exception as exc:
                message = f"{type(exc).__name__}: {exc}"
                result.target_errors[target_key] = message
                target.update_watch_target(
                    model, csc, enabled=True, successful=False, error=message
                )
                target.record_failure(
                    run_id=run_id,
                    source="samsung_fus",
                    operation="discover",
                    message=message,
                    details={"model": model, "csc": csc},
                )
                log_event(
                    LOGGER,
                    logging.ERROR,
                    "Samsung target discovery failed",
                    model=model,
                    csc=csc,
                    operation="history",
                    result="failed",
                )
        status = "PARTIAL" if result.target_errors else "SUCCESS"
        target.finish_run(
            run_id,
            status,
            {
                "targets": len([item for item in config.targets if item.enabled]),
                "candidates": result.candidates,
                "new_releases": result.new_releases,
                "matched_observations": result.matched_observations,
                "target_errors": len(result.target_errors),
            },
        )
    except Exception:
        target.finish_run(run_id, "FAILED", {"candidates": result.candidates})
        raise
    finally:
        if dry_run:
            target.close()
    return result


def probe(database: Database, backend: SamsungBackend, *, first: int) -> list[ProbeResult]:
    rows = database.list_releases(limit=first)
    histories: dict[tuple[str, str], tuple[str, ...] | Exception] = {}
    results: list[ProbeResult] = []
    for row in rows:
        key = (str(row["model"]), str(row["sales_csc"]))
        if key not in histories:
            try:
                histories[key] = backend.history(*key)
            except Exception as exc:
                histories[key] = exc
        history = histories[key]
        version = str(row["full_version"] or row["ap_version"])
        if isinstance(history, Exception):
            results.append(ProbeResult(str(row["id"]), *key, version, False, str(history)))
            continue
        if version in history:
            if not row["full_version"]:
                database.set_resolved_version(str(row["id"]), version)
            results.append(
                ProbeResult(
                    str(row["id"]), *key, version, True, "present in official Samsung history"
                )
            )
        else:
            results.append(
                ProbeResult(
                    str(row["id"]), *key, version, False, "not present in current Samsung history"
                )
            )
    return results


def _release_directory(root: Path, row: Any) -> Path:
    return root / str(row["model"]) / str(row["ap_version"]) / str(row["id"])


def download_release(
    database: Database,
    config: AppConfig,
    backend: SamsungBackend,
    release_id: str,
) -> Path:
    row = database.get_release(release_id)
    if row is None:
        raise KeyError(release_id)
    full_version = row["full_version"]
    if not full_version:
        raise ValueError("release must have an exact official Samsung version before download")
    final_dir = _release_directory(config.paths.downloads, row)
    final_path = final_dir / "firmware.zip"
    partial_path = final_dir / "firmware.zip.partial"
    cataloged = database.connection.execute(
        "SELECT path FROM artifact WHERE firmware_release_id = ? "
        "AND kind = 'decrypted_zip' AND status = 'VERIFIED' ORDER BY id DESC LIMIT 1",
        (release_id,),
    ).fetchone()
    if cataloged and Path(cataloged["path"]).is_file():
        artifact_path = Path(cataloged["path"])
        verify_zip(artifact_path, config.extract)
        return artifact_path
    if final_path.exists():
        _reconcile_existing_zip(database, config, release_id, final_path)
        return final_path
    usage_base = (
        config.paths.downloads.parent if config.paths.downloads.parent.exists() else Path("/")
    )
    free = shutil.disk_usage(usage_base).free
    expected = int(row["expected_size"] or 0)
    required = max(config.download.minimum_free_bytes, expected * 2)
    if free < required:
        raise OSError(f"insufficient free space: need {required} bytes, have {free}")
    database.queue(release_id)
    database.set_state(release_id, ReleaseState.DOWNLOADING)
    final_dir.mkdir(mode=0o750, parents=True, exist_ok=True)
    try:
        backend.download(str(row["model"]), str(row["sales_csc"]), str(full_version), partial_path)
        database.set_state(release_id, ReleaseState.DOWNLOADED)
        verify_zip(partial_path, config.extract)
        digest = sha256_file(partial_path)
        size = partial_path.stat().st_size
        database.set_state(release_id, ReleaseState.VERIFIED)
        existing = database.find_binary_blob(digest)
        if existing and Path(existing["path"]).is_file():
            partial_path.unlink(missing_ok=True)
            artifact_path = Path(existing["path"])
        else:
            os.replace(partial_path, final_path)
            artifact_path = final_path
            database.record_binary_blob(digest, final_path, size)
        database.record_artifact(
            release_id=release_id,
            kind="decrypted_zip",
            path=artifact_path,
            size=size,
            sha256=digest,
            status="VERIFIED",
        )
        database.set_state(release_id, ReleaseState.DECRYPTED)
        return artifact_path
    except Exception:
        partial_path.unlink(missing_ok=True)
        current = database.get_release(release_id)
        if current and current["state"] != ReleaseState.FAILED.value:
            database.set_state(release_id, ReleaseState.FAILED)
        raise


def _reconcile_existing_zip(
    database: Database,
    config: AppConfig,
    release_id: str,
    path: Path,
) -> None:
    """Catalog an atomically completed ZIP left ahead of a database commit."""
    verify_zip(path, config.extract)
    digest = sha256_file(path)
    size = path.stat().st_size
    database.record_binary_blob(digest, path, size)
    database.record_artifact(
        release_id=release_id,
        kind="decrypted_zip",
        path=path,
        size=size,
        sha256=digest,
        status="VERIFIED",
    )
    row = database.get_release(release_id)
    if row is None:
        raise KeyError(release_id)
    state = ReleaseState(row["state"])
    if state in {ReleaseState.DISCOVERED, ReleaseState.RESOLVED, ReleaseState.FAILED}:
        database.queue(release_id)
        database.set_state(release_id, ReleaseState.DOWNLOADING)
        state = ReleaseState.DOWNLOADING
    if state == ReleaseState.QUEUED:
        database.set_state(release_id, ReleaseState.DOWNLOADING)
        state = ReleaseState.DOWNLOADING
    if state == ReleaseState.DOWNLOADING:
        database.set_state(release_id, ReleaseState.DOWNLOADED)
        state = ReleaseState.DOWNLOADED
    if state == ReleaseState.DOWNLOADED:
        database.set_state(release_id, ReleaseState.VERIFIED)
        state = ReleaseState.VERIFIED
    if state == ReleaseState.VERIFIED:
        database.set_state(release_id, ReleaseState.DECRYPTED)


def extract_release(database: Database, config: AppConfig, release_id: str) -> Path:
    row = database.get_release(release_id)
    if row is None:
        raise KeyError(release_id)
    artifact = database.connection.execute(
        "SELECT * FROM artifact WHERE firmware_release_id = ? AND kind = 'decrypted_zip' "
        "AND status = 'VERIFIED' ORDER BY id DESC LIMIT 1",
        (release_id,),
    ).fetchone()
    if artifact is None:
        raise ArchiveError("no verified decrypted ZIP is catalogued for this release")
    destination = _release_directory(config.paths.extracted, row)
    manifest_path = destination / "manifest.json"
    if manifest_path.is_file():
        try:
            manifest_data = json.loads(manifest_path.read_text(encoding="utf-8"))
            if manifest_data["source_archive"]["sha256"] != artifact["sha256"]:
                raise ArchiveError("existing extraction manifest references a different ZIP")
            entries = [FileManifestEntry(**entry) for entry in manifest_data["files"]]
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ArchiveError("existing extraction manifest is invalid") from exc
    else:
        manifest_path, entries = extract_firmware(
            Path(artifact["path"]),
            destination,
            config.extract,
            release_metadata={
                "id": release_id,
                "model": row["model"],
                "sales_csc": row["sales_csc"],
                "ap_version": row["ap_version"],
                "full_version": row["full_version"],
            },
        )
    database.record_artifact(
        release_id=release_id,
        kind="manifest",
        path=manifest_path,
        size=manifest_path.stat().st_size,
        sha256=sha256_file(manifest_path),
        status="CATALOGED",
    )
    for entry in entries:
        database.record_artifact(
            release_id=release_id,
            kind="extracted_file",
            path=destination / entry.relative_path,
            size=entry.size,
            sha256=entry.sha256,
            status="CATALOGED",
        )
    current = database.get_release(release_id)
    if current is None:
        raise KeyError(release_id)
    if current["state"] != ReleaseState.EXTRACTED.value:
        database.set_state(release_id, ReleaseState.EXTRACTED)
    return manifest_path
