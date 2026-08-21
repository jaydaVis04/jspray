from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from jayspray.identity import normalized_csc, normalized_model


class ConfigurationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class PathsConfig:
    database: Path = Path("/var/lib/jayspray/database/firmware.db")
    downloads: Path = Path("/var/lib/jayspray/downloads")
    extracted: Path = Path("/var/lib/jayspray/extracted")
    cache: Path = Path("/var/lib/jayspray/cache")
    state: Path = Path("/var/lib/jayspray/state")


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
    samloader_executable: str = "/usr/local/bin/samloader"
    samloader_sha256: str | None = None
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


def _boolean(value: object, default: bool, name: str) -> bool:
    if value is None:
        return default
    if not isinstance(value, bool):
        raise ConfigurationError(f"{name} must be true or false")
    return value


def _integer(value: object, default: int, name: str) -> int:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigurationError(f"{name} must be an integer")
    return value


def _number(value: object, default: float, name: str) -> float:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigurationError(f"{name} must be a number")
    return float(value)


def _optional_sha256(value: object) -> str | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str) or len(value) != 64:
        raise ConfigurationError("download.samloader_sha256 must be a 64-character hex digest")
    try:
        bytes.fromhex(value)
    except ValueError as exc:
        raise ConfigurationError(
            "download.samloader_sha256 must be a 64-character hex digest"
        ) from exc
    return value.lower()


def load_config(path: Path | None = None) -> AppConfig:
    configured = path or Path(os.environ.get("JAYSPRAY_CONFIG", "/etc/jayspray/config.toml"))
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
                enabled=_boolean(target.get("enabled"), True, f"targets[{index}].enabled"),
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
            history_limit_per_target=_integer(
                discovery.get("history_limit_per_target"),
                default.discovery.history_limit_per_target,
                "discovery.history_limit_per_target",
            )
        ),
        download=DownloadConfig(
            automatic=_boolean(
                download.get("automatic"), default.download.automatic, "download.automatic"
            ),
            automatic_extract=_boolean(
                download.get("automatic_extract"),
                default.download.automatic_extract,
                "download.automatic_extract",
            ),
            concurrency=_integer(
                download.get("concurrency"), default.download.concurrency, "download.concurrency"
            ),
            connections_per_file=_integer(
                download.get("connections_per_file"),
                default.download.connections_per_file,
                "download.connections_per_file",
            ),
            minimum_free_bytes=_integer(
                download.get("minimum_free_bytes"),
                default.download.minimum_free_bytes,
                "download.minimum_free_bytes",
            ),
            samloader_executable=str(
                download.get("samloader_executable", default.download.samloader_executable)
            ),
            samloader_sha256=_optional_sha256(download.get("samloader_sha256")),
            command_timeout_seconds=_integer(
                download.get("command_timeout_seconds"),
                default.download.command_timeout_seconds,
                "download.command_timeout_seconds",
            ),
        ),
        extract=ExtractConfig(
            max_members=_integer(
                extract.get("max_members"), default.extract.max_members, "extract.max_members"
            ),
            max_total_bytes=_integer(
                extract.get("max_total_bytes"),
                default.extract.max_total_bytes,
                "extract.max_total_bytes",
            ),
            max_member_bytes=_integer(
                extract.get("max_member_bytes"),
                default.extract.max_member_bytes,
                "extract.max_member_bytes",
            ),
            max_compression_ratio=_number(
                extract.get("max_compression_ratio"),
                default.extract.max_compression_ratio,
                "extract.max_compression_ratio",
            ),
        ),
        logging_level=str(data.get("logging_level", default.logging_level)).upper(),
    )
    for value, name in (
        (cfg.discovery.history_limit_per_target, "discovery.history_limit_per_target"),
        (cfg.download.concurrency, "download.concurrency"),
        (cfg.download.connections_per_file, "download.connections_per_file"),
        (cfg.download.minimum_free_bytes, "download.minimum_free_bytes"),
        (cfg.download.command_timeout_seconds, "download.command_timeout_seconds"),
        (cfg.extract.max_members, "extract.max_members"),
        (cfg.extract.max_total_bytes, "extract.max_total_bytes"),
        (cfg.extract.max_member_bytes, "extract.max_member_bytes"),
        (cfg.extract.max_compression_ratio, "extract.max_compression_ratio"),
    ):
        _positive(value, name)
    if cfg.download.concurrency != 1:
        raise ConfigurationError("download.concurrency must be 1 in this release")
    if not Path(cfg.download.samloader_executable).is_absolute():
        raise ConfigurationError("download.samloader_executable must be an absolute path")
    if cfg.logging_level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
        raise ConfigurationError("logging_level must be DEBUG, INFO, WARNING, ERROR, or CRITICAL")
    return cfg
