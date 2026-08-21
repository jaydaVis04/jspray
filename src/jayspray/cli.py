from __future__ import annotations

import argparse
import json
import platform
import shutil
import sys
from pathlib import Path
from typing import Any

from jayspray.backend import SamloaderBackend
from jayspray.config import AppConfig, load_config
from jayspray.db import Database
from jayspray.identity import normalized_csc, normalized_model
from jayspray.lock import ProcessLock
from jayspray.logging import configure_logging, redact_text
from jayspray.metadata_index import ExternalMetadataIndex
from jayspray.models import TargetObservation, utc_now
from jayspray.orchestrator import discover, download_release, extract_release, probe
from jayspray.sources import configured_sources


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="jayspray", description="Headless Samsung firmware synchronization"
    )
    parser.add_argument("--config", type=Path, help="path to config.toml")
    sub = parser.add_subparsers(dest="command", required=True)

    discover_p = sub.add_parser("discover", help="discover recent model/region targets")
    discover_p.add_argument("--limit", type=int)
    discover_p.add_argument("--dry-run", action="store_true")

    sync_p = sub.add_parser("sync", help="discover targets and resolve Samsung's latest")
    sync_p.add_argument("--limit", type=int)
    sync_p.add_argument("--dry-run", action="store_true")

    probe_p = sub.add_parser("probe", help="resolve latest firmware without downloading")
    probe_p.add_argument("--first", type=int, required=True)

    download_p = sub.add_parser("download", help="resolve and download official firmware")
    group = download_p.add_mutually_exclusive_group(required=True)
    group.add_argument("--first", type=int, help="first N discovered model/region targets")
    group.add_argument("--id", dest="release_id", help="already resolved firmware release ID")
    group.add_argument("--model", help="Samsung model to resolve directly")
    download_p.add_argument("--region", "--csc", dest="region", help="Samsung region/CSC")
    download_p.add_argument(
        "--yes", action="store_true", help="confirm more than one large download"
    )

    extract_p = sub.add_parser("extract", help="extract and catalog a verified firmware ZIP")
    extract_p.add_argument("firmware_id", help="resolved firmware release ID")

    sub.add_parser("status", help="show source, target, run, failure, and disk status")

    search_p = sub.add_parser("search", help="search discovered model/region targets")
    search_p.add_argument("query", nargs="?")
    search_p.add_argument("--region", "--csc", dest="region")
    search_p.add_argument("--limit", type=int, default=100)

    show_p = sub.add_parser("show", help="show a target and its latest resolved release")
    show_p.add_argument("target_id")
    return parser


def _open_database(config: AppConfig) -> Database:
    database = Database(config.paths.database)
    database.migrate()
    return database


def _validate_count(value: int | None, name: str) -> int:
    if value is None or value < 1 or value > 10000:
        raise ValueError(f"{name} must be between 1 and 10000")
    return value


def _print_target(row: Any, database: Database) -> None:
    sources = database.target_sources(str(row["id"]))
    print(
        f"{str(row['id'])[:8]} {row['model']} {row['sales_csc']} "
        f"latest={row['latest_full_version'] or '-'} observed_by={'+'.join(sources)}"
    )


def _manual_target(database: Database, model: str, region: str) -> str:
    now = utc_now()
    normalized = normalized_model(model)
    normalized_region = normalized_csc(region)
    result = database.upsert_target_observation(
        TargetObservation(
            source="manual",
            source_record_key=f"{normalized}:{normalized_region}",
            source_url="manual:model-region",
            detail_url=None,
            model=normalized,
            sales_csc=normalized_region,
            source_updated_date=now.date().isoformat(),
            observed_at=now,
        )
    )
    return result.target_id


def _metadata_index(
    database: Database, config: AppConfig
) -> ExternalMetadataIndex | None:
    if config.metadata.path is None:
        return None
    index = ExternalMetadataIndex(database, config.metadata.path)
    index.refresh()
    return index


def _unique_target_ids_by_model(
    database: Database,
    target_ids: list[str],
    metadata_index: ExternalMetadataIndex | None,
) -> list[str]:
    selected: list[str] = []
    seen: set[str] = set()
    for target_id in target_ids:
        row = database.get_target(target_id)
        if row is None:
            continue
        model = normalized_model(str(row["model"]))
        if model in seen:
            print(f"SKIP TARGET {model} {row['sales_csc']} reason=model_already_selected")
            continue
        seen.add(model)
        if metadata_index is not None and metadata_index.contains(model):
            print(f"SKIP TARGET {model} {row['sales_csc']} reason=model_in_metadata")
            continue
        if database.model_has_verified_artifact(model):
            print(f"SKIP TARGET {model} {row['sales_csc']} reason=model_already_downloaded")
            continue
        selected.append(target_id)
    return selected


def _filter_download_rows(
    database: Database,
    rows: list[Any],
    metadata_index: ExternalMetadataIndex | None,
) -> list[Any]:
    selected: list[Any] = []
    seen: set[str] = set()
    for row in rows:
        model = normalized_model(str(row["model"]))
        if model in seen:
            print(f"SKIP DOWNLOAD {model} reason=model_already_selected")
            continue
        seen.add(model)
        if metadata_index is not None and metadata_index.contains(model):
            print(f"SKIP DOWNLOAD {model} reason=model_in_metadata")
            continue
        if database.model_has_verified_artifact(
            model, exclude_release_id=str(row["id"])
        ):
            print(f"SKIP DOWNLOAD {model} reason=model_already_downloaded")
            continue
        selected.append(row)
    return selected


def _append_completed_metadata(
    database: Database,
    config: AppConfig,
    metadata_index: ExternalMetadataIndex | None,
    row: Any,
    path: Path,
) -> None:
    if metadata_index is None or not config.metadata.append_completed:
        return
    artifact = database.connection.execute(
        "SELECT sha256 FROM artifact WHERE firmware_release_id = ? "
        "AND path = ? AND status = 'VERIFIED' ORDER BY id DESC LIMIT 1",
        (str(row["id"]), str(path)),
    ).fetchone()
    if artifact is None or not artifact["sha256"]:
        raise RuntimeError("verified firmware artifact has no SHA-256 for metadata append")
    metadata_index.append_completed(
        {
            "model": row["model"],
            "region": row["sales_csc"],
            "full_version": row["full_version"],
            "firmware_release_id": row["id"],
            "artifact": path,
            "sha256": artifact["sha256"],
            "completed_at": utc_now().isoformat(),
        }
    )


def _resolve_target_ids(
    database: Database,
    config: AppConfig,
    target_ids: list[str],
) -> tuple[SamloaderBackend, list[Any]]:
    backend = SamloaderBackend(config.download)
    results = probe(
        database,
        backend,
        first=max(1, len(target_ids)),
        target_ids=target_ids,
    )
    for item in results:
        tag = "RESOLVED" if item.resolvable else "RESOLVE FAIL"
        version = item.version or "-"
        print(
            f"{tag} {item.model} {item.sales_csc} {version} "
            f"{redact_text(item.reason)}"
        )
    return backend, results


def _run_discover(database: Database, config: AppConfig, args: argparse.Namespace) -> int:
    limit = None if args.limit is None else _validate_count(args.limit, "--limit")
    result = discover(
        database,
        config,
        configured_sources(config),
        limit=limit,
        dry_run=bool(args.dry_run),
        command=str(args.command),
    )
    label = "DRY RUN" if result.dry_run else "DISCOVERY"
    print(
        f"{label}: targets={result.candidates} new={result.new_targets} "
        f"matched={result.matched_observations} old_skipped={result.filtered_old} "
        f"undated_skipped={result.filtered_undated} "
        f"metadata_skipped={result.filtered_existing} "
        f"sources={','.join(config.discovery.sources)}"
    )
    for item in result.targets:
        print(
            f"{item['outcome']:19} {item['model']} {item['csc']} "
            f"date={item['source_date']} source={item['source']} "
            f"observed_by={'+'.join(item['sources'])}"
        )
    for source, error in result.source_errors.items():
        print(f"SOURCE FAIL {source}: {redact_text(error)}", file=sys.stderr)

    if args.command == "sync":
        discovered_targets = list(
            dict.fromkeys(str(item["id"]) for item in result.targets)
        )
        if result.dry_run:
            unique_items: list[dict[str, Any]] = []
            seen_models: set[str] = set()
            for item in result.targets:
                model = normalized_model(str(item["model"]))
                if model in seen_models:
                    print(
                        f"SKIP TARGET {model} {item['csc']} "
                        "reason=model_already_selected"
                    )
                    continue
                seen_models.add(model)
                unique_items.append(item)
            for item in unique_items:
                print(f"WOULD RESOLVE LATEST {item['model']} {item['csc']}")
                if config.download.automatic:
                    print(f"WOULD DOWNLOAD IF CHANGED {item['model']} {item['csc']}")
                else:
                    print(
                        f"SKIP DOWNLOAD {item['model']} {item['csc']} "
                        "reason=automatic_download_disabled"
                    )
            print(
                f"would_resolve={len(unique_items)} "
                f"automatic={str(config.download.automatic).lower()}"
            )
        elif discovered_targets:
            metadata_index = _metadata_index(database, config)
            unique_targets = _unique_target_ids_by_model(
                database, discovered_targets, metadata_index
            )
            backend, resolution = _resolve_target_ids(database, config, unique_targets)
            resolvable = [item for item in resolution if item.resolvable]
            if config.download.automatic:
                for item in resolvable:
                    try:
                        path = download_release(database, config, backend, item.release_id)
                        print(f"VERIFIED {path}")
                        row = database.get_release(item.release_id)
                        if row is None:
                            raise KeyError(item.release_id)
                        _append_completed_metadata(
                            database, config, metadata_index, row, path
                        )
                        if config.download.automatic_extract:
                            manifest = extract_release(database, config, item.release_id)
                            print(f"EXTRACTED manifest={manifest}")
                    except Exception as exc:
                        database.record_failure(
                            release_id=item.release_id,
                            source="samsung_fus",
                            operation="sync_download",
                            message=f"{type(exc).__name__}: {exc}",
                        )
                        print(
                            f"DOWNLOAD FAIL {item.model}/{item.sales_csc}: "
                            f"{redact_text(str(exc))}",
                            file=sys.stderr,
                        )
                        return 1
            if resolution and not resolvable:
                return 1
    return 1 if len(result.source_errors) == len(config.discovery.sources) else 0


def _status(database: Database, config: AppConfig) -> None:
    print(json.dumps(database.status_summary(), indent=2, sort_keys=True))
    paths = (("downloads", config.paths.downloads), ("extracted", config.paths.extracted))
    for label, path in paths:
        base = path if path.exists() else path.parent
        if base.exists():
            usage = shutil.disk_usage(base)
            print(f"disk {label}: free={usage.free} total={usage.total} path={path}")


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if platform.system() != "Linux":
        print(
            "jayspray is Linux-only; run it on Linux or in the supplied test container",
            file=sys.stderr,
        )
        return 2
    try:
        config = load_config(args.config)
        configure_logging(config.logging_level)
        dry_discovery = args.command in {"discover", "sync"} and bool(args.dry_run)
        database_context = (
            Database.readonly_snapshot(config.paths.database)
            if dry_discovery
            else _open_database(config)
        )
        with database_context as database:
            if args.command in {"discover", "sync"}:
                with ProcessLock(config.paths.state / "jayspray.lock"):
                    return _run_discover(database, config, args)
            if args.command == "probe":
                count = _validate_count(args.first, "--first")
                backend = SamloaderBackend(config.download)
                with ProcessLock(config.paths.state / "jayspray.lock"):
                    target_ids = [
                        str(row["id"]) for row in database.list_targets(limit=10_000)
                    ]
                    target_ids = _unique_target_ids_by_model(
                        database, target_ids, _metadata_index(database, config)
                    )[:count]
                    results = probe(
                        database, backend, first=count, target_ids=target_ids
                    )
                for item in results:
                    tag = "OK" if item.resolvable else "FAIL"
                    print(
                        f"[{tag}] {item.model} {item.sales_csc} {item.version or '-'} "
                        f"release={item.release_id or '-'} {redact_text(item.reason)}"
                    )
                return 1 if results and all(not item.resolvable for item in results) else 0
            if args.command == "download":
                backend = SamloaderBackend(config.download)
                with ProcessLock(config.paths.state / "jayspray.lock"):
                    metadata_index = _metadata_index(database, config)
                    if args.release_id:
                        selected_release = database.get_release(args.release_id)
                        candidate_rows: list[Any] = (
                            [selected_release] if selected_release is not None else []
                        )
                    else:
                        if args.model:
                            if not args.region:
                                raise ValueError("--model requires --region")
                            model = normalized_model(args.model)
                            if metadata_index is not None and metadata_index.contains(model):
                                print(f"SKIP TARGET {model} reason=model_in_metadata")
                                return 0
                            if database.model_has_verified_artifact(model):
                                print(f"SKIP TARGET {model} reason=model_already_downloaded")
                                return 0
                            target_ids = [_manual_target(database, args.model, args.region)]
                        else:
                            if args.region:
                                raise ValueError("--region is only valid with --model")
                            count = _validate_count(args.first, "--first")
                            target_ids = [
                                str(row["id"])
                                for row in database.list_targets(limit=10_000)
                            ]
                        target_ids = _unique_target_ids_by_model(
                            database, target_ids, metadata_index
                        )[:count if not args.model else 1]
                        if not target_ids:
                            print("No model/region targets selected after metadata checks.")
                            return 0
                        _, results = _resolve_target_ids(database, config, target_ids)
                        failed = [item for item in results if not item.resolvable]
                        if failed:
                            raise RuntimeError("one or more model/region targets could not resolve")
                        candidate_rows = [
                            row
                            for item in results
                            if (row := database.get_release(item.release_id)) is not None
                        ]
                    download_rows = _filter_download_rows(
                        database, candidate_rows, metadata_index
                    )
                    if not download_rows:
                        print("No firmware downloads selected after metadata/model checks.")
                        return 0
                    if len(download_rows) > 1 and not args.yes:
                        raise ValueError(
                            "multiple firmware downloads require --yes because packages are large"
                        )
                    print(
                        f"About to download {len(download_rows)} firmware package(s), "
                        "sequentially."
                    )
                    for row in download_rows:
                        print(
                            f"DOWNLOAD {row['model']} {row['sales_csc']} {row['full_version']} "
                            f"expected_size={row['expected_size']}"
                        )
                        path = download_release(database, config, backend, str(row["id"]))
                        print(f"VERIFIED {path}")
                        _append_completed_metadata(
                            database, config, metadata_index, row, path
                        )
                return 0
            if args.command == "extract":
                with ProcessLock(config.paths.state / "jayspray.lock"):
                    manifest = extract_release(database, config, args.firmware_id)
                print(f"EXTRACTED manifest={manifest}")
                return 0
            if args.command == "status":
                _status(database, config)
                return 0
            if args.command == "search":
                target_rows = database.search_targets(
                    args.query, csc=args.region, limit=args.limit
                )
                for row in target_rows:
                    _print_target(row, database)
                return 0
            if args.command == "show":
                target = database.get_target(args.target_id)
                if target is None:
                    raise KeyError(args.target_id)
                release = (
                    database.get_release(str(target["latest_release_id"]))
                    if target["latest_release_id"]
                    else None
                )
                artifacts = []
                if release is not None:
                    artifacts = [
                        dict(item)
                        for item in database.connection.execute(
                            "SELECT kind, path, size, sha256, status FROM artifact "
                            "WHERE firmware_release_id = ? ORDER BY id",
                            (release["id"],),
                        )
                    ]
                print(
                    json.dumps(
                        {
                            "target": dict(target),
                            "observed_by": database.target_sources(args.target_id),
                            "latest_release": dict(release) if release else None,
                            "artifacts": artifacts,
                        },
                        indent=2,
                        sort_keys=True,
                    )
                )
                return 0
    except (OSError, RuntimeError, ValueError, KeyError) as exc:
        print(f"ERROR: {redact_text(str(exc))}", file=sys.stderr)
        return 1
    return 0
