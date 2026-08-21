from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fwtool.identity import normalized_csc, normalized_model


class ConfigurationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class PathsConfig:
    database: Path = Path("/var/lib/samsung-fw-sync/database/firmware.db")
    downloads: Path = Path("/var/lib/samsung-fw-sync/downloads")
    extracted: Path = Path("/var/lib/samsung-fw-sync/extracted")
    cache: Path = Path("/var/lib/samsung-fw-sync/cache")
    state: Path = Path("/var/lib/samsung-fw-sync/state")


@dataclass(frozen=True, slots=True)
class TargetConfig:
    model: str
    csc: str
    enabled: bool = True


@dataclass(frozen=True, slots=True)
class DiscoveryConfig:
    history_limit_per_target: int = 5


@dataclass(frozen=True, slots=True)
class DownloadConfig:
    automatic: bool = False
    automatic_extract: bool = False
    concurrency: int = 1
    connections_per_file: int = 1
    minimum_free_bytes: int = 15 * 1024**3
    samloader_executable: str = "samloader"
    command_timeout_seconds: int = 6 * 60 * 60


@dataclass(frozen=True, slots=True)
class ExtractConfig:
    max_members: int = 5000
    max_total_bytes: int = 30 * 1024**3
    max_member_bytes: int = 12 * 1024**3
    max_compression_ratio: float = 1000.0


@dataclass(frozen=True, slots=True)
class AppConfig:
    paths: PathsConfig = field(default_factory=PathsConfig)
    targets: tuple[TargetConfig, ...] = ()
    discovery: DiscoveryConfig = field(default_factory=DiscoveryConfig)
    download: DownloadConfig = field(default_factory=DownloadConfig)
    extract: ExtractConfig = field(default_factory=ExtractConfig)
    logging_level: str = "INFO"


def _section(data: dict[str, Any], name: str) -> dict[str, Any]:
    value = data.get(name, {})
    if not isinstance(value, dict):
        raise ConfigurationError(f"[{name}] must be a TOML table")
    return value


def _path(value: object, default: Path) -> Path:
    if value is None:
        return default
    if not isinstance(value, str) or not value.startswith("/"):
        raise ConfigurationError("storage paths must be absolute Linux paths")
    return Path(value)


def _positive(value: int | float, name: str) -> None:
    if value <= 0:
        raise ConfigurationError(f"{name} must be positive")


def load_config(path: Path | None = None) -> AppConfig:
    configured = path or Path(os.environ.get("FWTOOL_CONFIG", "/etc/samsung-fw-sync/config.toml"))
    data: dict[str, Any] = {}
    if configured.exists():
        with configured.open("rb") as handle:
            parsed = tomllib.load(handle)
        if not isinstance(parsed, dict):
            raise ConfigurationError("configuration root must be a TOML table")
        data = parsed

    default = AppConfig()
    paths = _section(data, "paths")
    download = _section(data, "download")
    discovery = _section(data, "discovery")
    extract = _section(data, "extract")
    raw_targets = data.get("targets", [])
    if not isinstance(raw_targets, list):
        raise ConfigurationError("[[targets]] must be an array of TOML tables")
    targets: list[TargetConfig] = []
    seen_targets: set[tuple[str, str]] = set()
    for index, target in enumerate(raw_targets):
        if not isinstance(target, dict):
            raise ConfigurationError(f"targets[{index}] must be a TOML table")
        model = target.get("model")
        csc = target.get("csc")
        if not isinstance(model, str) or not isinstance(csc, str):
            raise ConfigurationError(f"targets[{index}] requires string model and csc")
        normalized = (normalized_model(model), normalized_csc(csc))
        if normalized in seen_targets:
            raise ConfigurationError(f"duplicate Samsung target: {normalized[0]}/{normalized[1]}")
        seen_targets.add(normalized)
        targets.append(
            TargetConfig(
                model=normalized[0],
                csc=normalized[1],
                enabled=bool(target.get("enabled", True)),
            )
        )

    cfg = AppConfig(
        paths=PathsConfig(
            database=_path(paths.get("database"), default.paths.database),
            downloads=_path(paths.get("downloads"), default.paths.downloads),
            extracted=_path(paths.get("extracted"), default.paths.extracted),
            cache=_path(paths.get("cache"), default.paths.cache),
            state=_path(paths.get("state"), default.paths.state),
        ),
        targets=tuple(targets),
        discovery=DiscoveryConfig(
            history_limit_per_target=int(
                discovery.get(
                    "history_limit_per_target", default.discovery.history_limit_per_target
                )
            )
        ),
        download=DownloadConfig(
            automatic=bool(download.get("automatic", default.download.automatic)),
            automatic_extract=bool(
                download.get("automatic_extract", default.download.automatic_extract)
            ),
            concurrency=int(download.get("concurrency", default.download.concurrency)),
            connections_per_file=int(
                download.get("connections_per_file", default.download.connections_per_file)
            ),
            minimum_free_bytes=int(
                download.get("minimum_free_bytes", default.download.minimum_free_bytes)
            ),
            samloader_executable=str(
                download.get("samloader_executable", default.download.samloader_executable)
            ),
            command_timeout_seconds=int(
                download.get("command_timeout_seconds", default.download.command_timeout_seconds)
            ),
        ),
        extract=ExtractConfig(
            max_members=int(extract.get("max_members", default.extract.max_members)),
            max_total_bytes=int(extract.get("max_total_bytes", default.extract.max_total_bytes)),
            max_member_bytes=int(extract.get("max_member_bytes", default.extract.max_member_bytes)),
            max_compression_ratio=float(
                extract.get("max_compression_ratio", default.extract.max_compression_ratio)
            ),
        ),
        logging_level=str(data.get("logging_level", default.logging_level)).upper(),
    )
    for value, name in (
        (cfg.discovery.history_limit_per_target, "discovery.history_limit_per_target"),
        (cfg.download.concurrency, "download.concurrency"),
        (cfg.download.connections_per_file, "download.connections_per_file"),
        (cfg.extract.max_members, "extract.max_members"),
    ):
        _positive(value, name)
    if cfg.download.concurrency != 1:
        raise ConfigurationError("download.concurrency must be 1 in this release")
    return cfg
