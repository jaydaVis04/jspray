from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


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
class HttpConfig:
    timeout_seconds: int = 30
    retries: int = 3
    retry_base_seconds: float = 1.0
    request_delay_seconds: float = 1.0
    max_response_bytes: int = 5 * 1024**2
    user_agent: str = "JAYSPRAY/0.3.0"


@dataclass(frozen=True, slots=True)
class DiscoveryConfig:
    sources: tuple[str, ...] = ("samfrew", "sammobile")
    pages_per_source: int = 1
    lookback_days: int = 21
    http: HttpConfig = field(default_factory=HttpConfig)


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
class MetadataConfig:
    path: Path | None = None
    append_completed: bool = False


@dataclass(frozen=True, slots=True)
class AppConfig:
    paths: PathsConfig = field(default_factory=PathsConfig)
    discovery: DiscoveryConfig = field(default_factory=DiscoveryConfig)
    download: DownloadConfig = field(default_factory=DownloadConfig)
    extract: ExtractConfig = field(default_factory=ExtractConfig)
    metadata: MetadataConfig = field(default_factory=MetadataConfig)
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


def _optional_path(value: object) -> Path | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str) or not value.startswith("/"):
        raise ConfigurationError("metadata.path must be an absolute Linux path")
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


def _string(value: object, default: str, name: str) -> str:
    if value is None:
        return default
    if not isinstance(value, str):
        raise ConfigurationError(f"{name} must be a string")
    return value


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


def _sources(value: object, default: tuple[str, ...]) -> tuple[str, ...]:
    if value is None:
        return default
    if not isinstance(value, list) or not value or not all(isinstance(item, str) for item in value):
        raise ConfigurationError("discovery.sources must be a non-empty array of strings")
    normalized = tuple(item.strip().lower() for item in value)
    supported = {"samfrew", "samfw", "sammobile"}
    unknown = set(normalized) - supported
    if unknown:
        raise ConfigurationError(f"unsupported discovery source(s): {', '.join(sorted(unknown))}")
    if len(set(normalized)) != len(normalized):
        raise ConfigurationError("discovery.sources must not contain duplicates")
    return normalized


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
    http = _section(data, "http")
    extract = _section(data, "extract")
    metadata = _section(data, "metadata")
    cfg = AppConfig(
        paths=PathsConfig(
            database=_path(paths.get("database"), default.paths.database),
            downloads=_path(paths.get("downloads"), default.paths.downloads),
            extracted=_path(paths.get("extracted"), default.paths.extracted),
            cache=_path(paths.get("cache"), default.paths.cache),
            state=_path(paths.get("state"), default.paths.state),
        ),
        discovery=DiscoveryConfig(
            sources=_sources(discovery.get("sources"), default.discovery.sources),
            pages_per_source=_integer(
                discovery.get("pages_per_source"),
                default.discovery.pages_per_source,
                "discovery.pages_per_source",
            ),
            lookback_days=_integer(
                discovery.get("lookback_days"),
                default.discovery.lookback_days,
                "discovery.lookback_days",
            ),
            http=HttpConfig(
                timeout_seconds=_integer(
                    http.get("timeout_seconds"),
                    default.discovery.http.timeout_seconds,
                    "http.timeout_seconds",
                ),
                retries=_integer(
                    http.get("retries"), default.discovery.http.retries, "http.retries"
                ),
                retry_base_seconds=_number(
                    http.get("retry_base_seconds"),
                    default.discovery.http.retry_base_seconds,
                    "http.retry_base_seconds",
                ),
                request_delay_seconds=_number(
                    http.get("request_delay_seconds"),
                    default.discovery.http.request_delay_seconds,
                    "http.request_delay_seconds",
                ),
                max_response_bytes=_integer(
                    http.get("max_response_bytes"),
                    default.discovery.http.max_response_bytes,
                    "http.max_response_bytes",
                ),
                user_agent=_string(
                    http.get("user_agent"),
                    default.discovery.http.user_agent,
                    "http.user_agent",
                ),
            ),
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
        metadata=MetadataConfig(
            path=_optional_path(metadata.get("path")),
            append_completed=_boolean(
                metadata.get("append_completed"),
                default.metadata.append_completed,
                "metadata.append_completed",
            ),
        ),
        logging_level=str(data.get("logging_level", default.logging_level)).upper(),
    )
    for value, name in (
        (cfg.discovery.pages_per_source, "discovery.pages_per_source"),
        (cfg.discovery.lookback_days, "discovery.lookback_days"),
        (cfg.discovery.http.timeout_seconds, "http.timeout_seconds"),
        (cfg.discovery.http.retry_base_seconds, "http.retry_base_seconds"),
        (cfg.discovery.http.request_delay_seconds, "http.request_delay_seconds"),
        (cfg.discovery.http.max_response_bytes, "http.max_response_bytes"),
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
    if cfg.discovery.http.retries < 0 or cfg.discovery.http.retries > 10:
        raise ConfigurationError("http.retries must be between 0 and 10")
    if cfg.discovery.pages_per_source > 100:
        raise ConfigurationError("discovery.pages_per_source must not exceed 100")
    if cfg.discovery.lookback_days > 365:
        raise ConfigurationError("discovery.lookback_days must not exceed 365")
    if cfg.discovery.http.timeout_seconds > 600:
        raise ConfigurationError("http.timeout_seconds must not exceed 600")
    if cfg.discovery.http.max_response_bytes > 20 * 1024**2:
        raise ConfigurationError("http.max_response_bytes must not exceed 20971520")
    if "\n" in cfg.discovery.http.user_agent or "\r" in cfg.discovery.http.user_agent:
        raise ConfigurationError("http.user_agent must be a single line")
    if not cfg.discovery.http.user_agent.strip():
        raise ConfigurationError("http.user_agent must not be empty")
    if len(cfg.discovery.http.user_agent) > 256:
        raise ConfigurationError("http.user_agent must not exceed 256 characters")
    if cfg.download.concurrency != 1:
        raise ConfigurationError("download.concurrency must be 1 in this release")
    if cfg.metadata.append_completed and cfg.metadata.path is None:
        raise ConfigurationError("metadata.append_completed requires metadata.path")
    if not Path(cfg.download.samloader_executable).is_absolute():
        raise ConfigurationError("download.samloader_executable must be an absolute path")
    if cfg.logging_level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
        raise ConfigurationError("logging_level must be DEBUG, INFO, WARNING, ERROR, or CRITICAL")
    return cfg
