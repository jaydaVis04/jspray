from __future__ import annotations

import argparse
import json
import platform
import shutil
import sys
from pathlib import Path
from typing import Any

from fwtool.backend import SamloaderBackend
from fwtool.config import AppConfig, load_config
from fwtool.db import Database
from fwtool.lock import ProcessLock
from fwtool.logging import configure_logging
from fwtool.orchestrator import discover, download_release, extract_release, probe


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="fwtool", description="Samsung firmware synchronization")
    parser.add_argument("--config", type=Path, help="path to config.toml")
    sub = parser.add_subparsers(dest="command", required=True)

    discover_p = sub.add_parser("discover", help="discover metadata without downloading")
    discover_p.add_argument("--limit", type=int)
    discover_p.add_argument("--dry-run", action="store_true")

    sync_p = sub.add_parser("sync", help="discover and apply configured queue policy")
    sync_p.add_argument("--limit", type=int)
    sync_p.add_argument("--dry-run", action="store_true")

    probe_p = sub.add_parser("probe", help="probe Samsung history without a payload download")
    probe_p.add_argument("--first", type=int, required=True)

    inspect_p = sub.add_parser(
        "inspect", help="compare official Samsung PDA history across CSC routes"
    )
    inspect_p.add_argument("model", help="Samsung model, for example SM-S928U1")
    inspect_p.add_argument("--csc", action="append", required=True, help="repeat for each CSC")
    inspect_p.add_argument("--history-limit", type=int, default=5)

    download_p = sub.add_parser("download", help="download exact probed firmware releases")
    group = download_p.add_mutually_exclusive_group(required=True)
    group.add_argument("--first", type=int)
    group.add_argument("--id", dest="release_id")
    download_p.add_argument(
        "--yes", action="store_true", help="confirm more than one large download"
    )

    extract_p = sub.add_parser("extract", help="extract and catalog a verified firmware ZIP")
    extract_p.add_argument("firmware_id")

    sub.add_parser("status", help="show run, queue, failure, and disk status")

    search_p = sub.add_parser("search", help="search the local firmware catalog")
    search_p.add_argument("query", nargs="?")
    search_p.add_argument("--csc")
    search_p.add_argument("--pda")
    search_p.add_argument("--limit", type=int, default=100)

    show_p = sub.add_parser("show", help="show a canonical release and all Samsung CSC routes")
    show_p.add_argument("firmware_id")

    backfill_p = sub.add_parser("backfill", help="explicit bounded Samsung history discovery")
    backfill_p.add_argument("--history-limit-per-target", type=int, required=True)
    backfill_p.add_argument("--limit", type=int)
    return parser


def _print_release(row: Any, database: Database) -> None:
    routes = database.route_observations(str(row["id"]))
    print(
        f"{str(row['id'])[:8]} {row['model']} {row['sales_csc']} {row['ap_version']} "
        f"state={row['state']} full_version={row['full_version'] or '-'} "
        f"Samsung_routes={len(routes)}"
    )


def _open_database(config: AppConfig) -> Database:
    database = Database(config.paths.database)
    database.migrate()
    return database


def _validate_count(value: int | None, name: str) -> int:
    if value is None or value < 1 or value > 10000:
        raise ValueError(f"{name} must be between 1 and 10000")
    return value


def _run_discover(database: Database, config: AppConfig, args: argparse.Namespace) -> int:
    limit = None if args.limit is None else _validate_count(args.limit, "--limit")
    backend = SamloaderBackend(config.download)
    result = discover(
        database,
        config,
        backend,
        limit=limit,
        dry_run=bool(args.dry_run),
        command=str(args.command),
    )
    label = "DRY RUN" if result.dry_run else "DISCOVERY"
    print(
        f"{label}: candidates={result.candidates} new={result.new_releases} "
        f"matched={result.matched_observations} upstream=Samsung"
    )
    for item in result.releases:
        print(f"{item['outcome']:19} {item['model']} {item['csc']} {item['version']}")
    for target, error in result.target_errors.items():
        print(f"TARGET FAIL {target}: {error}", file=sys.stderr)
    if args.command == "sync":
        if result.dry_run:
            new_ids = {item["id"] for item in result.releases if item["outcome"] == "new_release"}
            would_download = len(new_ids) if config.download.automatic else 0
            print(
                f"would_queue={len(new_ids)} would_download={would_download} "
                f"automatic={str(config.download.automatic).lower()}"
            )
        elif config.download.automatic:
            failures = 0
            new_release_ids = list(
                dict.fromkeys(
                    item["id"] for item in result.releases if item["outcome"] == "new_release"
                )
            )
            for release_id in new_release_ids:
                try:
                    path = download_release(database, config, backend, str(release_id))
                    print(f"VERIFIED {path}")
                    if config.download.automatic_extract:
                        manifest = extract_release(database, config, str(release_id))
                        print(f"EXTRACTED manifest={manifest}")
                except Exception as exc:
                    failures += 1
                    database.record_failure(
                        release_id=str(release_id),
                        source="samsung_fus",
                        operation="sync_download",
                        message=f"{type(exc).__name__}: {exc}",
                    )
                    print(f"DOWNLOAD FAIL {str(release_id)[:8]}: {exc}", file=sys.stderr)
            if failures:
                return 1
    enabled_targets = len([target for target in config.targets if target.enabled])
    return 1 if enabled_targets and len(result.target_errors) == enabled_targets else 0


def _status(database: Database, config: AppConfig) -> None:
    status = database.status_summary()
    print(json.dumps(status, indent=2, sort_keys=True))
    for label, path in (
        ("downloads", config.paths.downloads),
        ("extracted", config.paths.extracted),
    ):
        base = path if path.exists() else path.parent
        if base.exists():
            usage = shutil.disk_usage(base)
            print(f"disk {label}: free={usage.free} total={usage.total} path={path}")


def _run_inspect(config: AppConfig, args: argparse.Namespace) -> int:
    history_limit = _validate_count(args.history_limit, "--history-limit")
    backend = SamloaderBackend(config.download)
    by_pda: dict[str, list[tuple[str, str]]] = {}
    failures = 0
    for csc in args.csc:
        try:
            for version in backend.history(args.model, csc)[:history_limit]:
                pda = version.split("/", 1)[0]
                by_pda.setdefault(pda, []).append((csc.upper(), version))
        except Exception as exc:
            failures += 1
            print(f"[FAIL] {args.model}/{csc.upper()}: {exc}", file=sys.stderr)
    for pda, routes in by_pda.items():
        cscs = ",".join(route[0] for route in routes)
        disposition = "MERGE" if len(routes) > 1 else "UNIQUE"
        print(f"[{disposition}] {args.model.upper()} PDA={pda} routes={len(routes)} CSC={cscs}")
        for csc, version in routes:
            print(f"  {csc}: {version}")
    return 1 if failures == len(args.csc) else 0


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if platform.system() != "Linux":
        print(
            "fwtool is Linux-only; run it on a Linux host or in the supplied Linux test container",
            file=sys.stderr,
        )
        return 2
    try:
        config = load_config(args.config)
        configure_logging(config.logging_level)
        if args.command == "inspect":
            return _run_inspect(config, args)
        dry_discovery = args.command in {"discover", "sync"} and bool(args.dry_run)
        database_context = (
            Database.readonly_snapshot(config.paths.database)
            if dry_discovery
            else _open_database(config)
        )
        with database_context as database:
            if args.command in {"discover", "sync", "backfill"}:
                with ProcessLock(config.paths.state / "fwtool.lock"):
                    if args.command == "backfill":
                        history_limit = _validate_count(
                            args.history_limit_per_target, "--history-limit-per-target"
                        )
                        limit = (
                            None if args.limit is None else _validate_count(args.limit, "--limit")
                        )
                        backend = SamloaderBackend(config.download)
                        result = discover(
                            database,
                            config,
                            backend,
                            history_limit_per_target=history_limit,
                            limit=limit,
                            command="backfill",
                        )
                        print(
                            f"BACKFILL: candidates={result.candidates} new={result.new_releases} "
                            f"matched={result.matched_observations} upstream=Samsung"
                        )
                        enabled_targets = len(
                            [target for target in config.targets if target.enabled]
                        )
                        return (
                            1
                            if enabled_targets and len(result.target_errors) == enabled_targets
                            else 0
                        )
                    return _run_discover(database, config, args)
            if args.command == "probe":
                count = _validate_count(args.first, "--first")
                backend = SamloaderBackend(config.download)
                with ProcessLock(config.paths.state / "fwtool.lock"):
                    results = probe(database, backend, first=count)
                for probe_result in results:
                    tag = "OK" if probe_result.resolvable else "FAIL"
                    print(
                        f"[{tag}] {probe_result.model} {probe_result.sales_csc} "
                        f"{probe_result.version} {probe_result.reason}"
                    )
                return 1 if any(not item.resolvable for item in results) else 0
            if args.command == "download":
                rows = (
                    [database.get_release(args.release_id)]
                    if args.release_id
                    else database.list_releases(
                        limit=_validate_count(args.first, "--first"),
                        states=("DISCOVERED", "RESOLVED", "QUEUED", "FAILED"),
                    )
                )
                rows = [row for row in rows if row is not None]
                if len(rows) > 1 and not args.yes:
                    raise ValueError(
                        "multiple firmware downloads require --yes because packages are large"
                    )
                backend = SamloaderBackend(config.download)
                print(f"About to download {len(rows)} firmware package(s), sequentially.")
                with ProcessLock(config.paths.state / "fwtool.lock"):
                    for row in rows:
                        print(
                            f"DOWNLOAD {row['model']} {row['sales_csc']} "
                            f"{row['full_version'] or row['ap_version']} "
                            f"expected_size={row['expected_size']}"
                        )
                        path = download_release(database, config, backend, str(row["id"]))
                        print(f"VERIFIED {path}")
                return 0
            if args.command == "extract":
                with ProcessLock(config.paths.state / "fwtool.lock"):
                    manifest = extract_release(database, config, args.firmware_id)
                print(f"EXTRACTED manifest={manifest}")
                return 0
            if args.command == "status":
                _status(database, config)
                return 0
            if args.command == "search":
                rows = database.search(args.query, csc=args.csc, pda=args.pda, limit=args.limit)
                for row in rows:
                    _print_release(row, database)
                return 0
            if args.command == "show":
                show_row = database.get_release(args.firmware_id)
                if show_row is None:
                    raise KeyError(args.firmware_id)
                artifacts = [
                    dict(item)
                    for item in database.connection.execute(
                        "SELECT kind, path, size, sha256, status FROM artifact "
                        "WHERE firmware_release_id = ? ORDER BY id",
                        (args.firmware_id,),
                    )
                ]
                print(
                    json.dumps(
                        {
                            "release": dict(show_row),
                            "Samsung_routes": database.route_observations(args.firmware_id),
                            "artifacts": artifacts,
                        },
                        indent=2,
                        sort_keys=True,
                    )
                )
                return 0
    except (OSError, RuntimeError, ValueError, KeyError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0
