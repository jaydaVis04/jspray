from __future__ import annotations

import json
import logging
import os
import shutil
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from jayspray.backend.base import SamsungBackend
from jayspray.config import AppConfig
from jayspray.db import Database
from jayspray.extract import (
    ArchiveError,
    FileManifestEntry,
    extract_firmware,
    sha256_file,
    verify_zip,
)
from jayspray.identity import full_version_components
from jayspray.logging import log_event
from jayspray.metadata_index import ExternalMetadataIndex
from jayspray.models import FirmwareObservation, ProbeResult, ReleaseState, TargetObservation
from jayspray.sources.base import FirmwareSource

LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class DiscoveryResult:
    new_releases: int = 0
    matched_observations: int = 0
    candidates: int = 0
    releases: list[dict[str, Any]] = field(default_factory=list)
    target_errors: dict[str, str] = field(default_factory=dict)
    source_errors: dict[str, str] = field(default_factory=dict)
    dry_run: bool = False


@dataclass(slots=True)
class TargetDiscoveryResult:
    new_targets: int = 0
    matched_observations: int = 0
    candidates: int = 0
    filtered_old: int = 0
    filtered_undated: int = 0
    filtered_existing: int = 0
    targets: list[dict[str, Any]] = field(default_factory=list)
    source_errors: dict[str, str] = field(default_factory=dict)
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


def _source_date(observation: TargetObservation) -> date | None:
    value = observation.source_updated_date or ""
    for pattern in ("%Y-%m-%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(value, pattern).date()
        except ValueError:
            continue
    return None


def _fetch_source(source: FirmwareSource, pages: int) -> tuple[TargetObservation, ...]:
    observations: list[TargetObservation] = []
    page = 0
    for _ in range(pages):
        result = source.fetch_page(page)
        observations.extend(result.observations)
        if result.next_page is None or not result.observations:
            break
        page = result.next_page
    return tuple(observations)


def discover(
    database: Database,
    config: AppConfig,
    sources: tuple[FirmwareSource, ...],
    *,
    limit: int | None = None,
    dry_run: bool = False,
    pages_per_source: int | None = None,
    command: str = "discover",
) -> TargetDiscoveryResult:
    """Discover recent model/CSC targets without trusting index firmware versions."""
    if not sources:
        raise ValueError("no firmware discovery sources are enabled")
    target = database.clone_in_memory() if dry_run else database
    result = TargetDiscoveryResult(dry_run=dry_run)
    run_id = target.start_run(command, dry_run=dry_run)
    pages = pages_per_source or config.discovery.pages_per_source
    fetched: dict[str, tuple[TargetObservation, ...]] = {}
    try:
        with ThreadPoolExecutor(max_workers=len(sources), thread_name_prefix="discovery") as pool:
            futures = {pool.submit(_fetch_source, source, pages): source for source in sources}
            for future in as_completed(futures):
                source = futures[future]
                try:
                    fetched[source.name] = future.result()
                    latest_key = (
                        fetched[source.name][0].source_record_key if fetched[source.name] else None
                    )
                    target.update_source_checkpoint(
                        source.name, successful=True, latest_record_key=latest_key
                    )
                except Exception as exc:
                    message = f"{type(exc).__name__}: {exc}"
                    result.source_errors[source.name] = message
                    target.update_source_checkpoint(source.name, successful=False, error=message)
                    target.record_failure(
                        run_id=run_id,
                        source=source.name,
                        operation="discover",
                        message=message,
                    )
                    log_event(
                        LOGGER,
                        logging.ERROR,
                        "Firmware index discovery failed",
                        source=source.name,
                        operation="discover",
                        result="failed",
                    )

        ordered: list[TargetObservation] = []
        source_order = {source.name: index for index, source in enumerate(sources)}
        for values in fetched.values():
            ordered.extend(values)
        ordered.sort(
            key=lambda item: (
                _source_date(item) or date.min,
                -source_order.get(item.source, 999),
            ),
            reverse=True,
        )

        cutoff = datetime.now(UTC).date() - timedelta(days=config.discovery.lookback_days)
        metadata_index = None
        if config.metadata.path is not None:
            metadata_index = ExternalMetadataIndex(target, config.metadata.path)
            metadata_index.refresh()
        eligible: list[TargetObservation] = []
        for item in ordered:
            source_date = _source_date(item)
            if source_date is None:
                result.filtered_undated += 1
            elif source_date < cutoff:
                result.filtered_old += 1
            elif metadata_index is not None and metadata_index.contains(item.model):
                result.filtered_existing += 1
            else:
                eligible.append(item)

        selected_keys: set[tuple[str, str]] | None = None
        if limit is not None:
            selected_keys = set()
            for item in eligible:
                selected_keys.add((item.model.upper(), item.sales_csc.upper()))
                if len(selected_keys) >= limit:
                    break

        for observation in eligible:
            if (
                selected_keys is not None
                and (observation.model.upper(), observation.sales_csc.upper())
                not in selected_keys
            ):
                continue
            merged = target.upsert_target_observation(observation)
            result.candidates += 1
            if merged.outcome == "new_target":
                result.new_targets += 1
            else:
                result.matched_observations += 1
            target_row = target.get_target(merged.target_id)
            if target_row is None:
                raise KeyError(merged.target_id)
            result.targets.append(
                {
                    "id": merged.target_id,
                    "source": observation.source,
                    "model": observation.model,
                    "csc": observation.sales_csc,
                    "outcome": merged.outcome,
                    "source_count": merged.source_count,
                    "sources": target.target_sources(merged.target_id),
                    "source_date": observation.source_updated_date,
                    "latest_release_id": target_row["latest_release_id"],
                    "latest_full_version": target_row["latest_full_version"],
                }
            )
        if not fetched:
            status = "FAILED"
        elif result.source_errors:
            status = "PARTIAL"
        else:
            status = "SUCCESS"
        target.finish_run(
            run_id,
            status,
            {
                "sources_enabled": len(sources),
                "sources_succeeded": len(fetched),
                "candidates": result.candidates,
                "new_targets": result.new_targets,
                "matched_observations": result.matched_observations,
                "filtered_old": result.filtered_old,
                "filtered_undated": result.filtered_undated,
                "filtered_existing": result.filtered_existing,
            },
        )
    except Exception:
        target.finish_run(run_id, "FAILED", {"candidates": result.candidates})
        raise
    finally:
        if dry_run:
            target.close()
    return result


def probe(
    database: Database,
    backend: SamsungBackend,
    *,
    first: int,
    target_ids: list[str] | None = None,
) -> list[ProbeResult]:
    if target_ids is None:
        rows = database.list_targets(limit=first)
    else:
        rows = [row for item in target_ids if (row := database.get_target(item)) is not None]
    results: list[ProbeResult] = []
    for row in rows:
        target_id = str(row["id"])
        model = str(row["model"])
        csc = str(row["sales_csc"])
        try:
            history = backend.history(model, csc)
            if not history:
                raise RuntimeError("Samsung returned no firmware version")
            full_version = history[0]
            merged = database.upsert_observation(_official_observation(model, csc, full_version))
            database.set_resolved_version(
                merged.release_id, full_version, sales_csc=csc
            )
            database.set_target_resolution(
                target_id,
                release_id=merged.release_id,
                full_version=full_version,
            )
            results.append(
                ProbeResult(
                    target_id,
                    merged.release_id,
                    model,
                    csc,
                    full_version,
                    True,
                    "latest firmware resolved from Samsung for model and region",
                )
            )
        except Exception as exc:
            reason = f"{type(exc).__name__}: {exc}"
            database.set_target_resolution(
                target_id, release_id=None, full_version=None, error=reason
            )
            results.append(ProbeResult(target_id, "", model, csc, "", False, reason))
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
    if final_path.is_symlink() or partial_path.is_symlink():
        raise OSError("refusing a symlink at a managed firmware path")
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
    partial_path.unlink(missing_ok=True)
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
    if manifest_path.is_symlink():
        raise ArchiveError("existing extraction manifest must not be a symlink")
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
