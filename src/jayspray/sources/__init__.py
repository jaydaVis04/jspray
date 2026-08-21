from jayspray.config import AppConfig
from jayspray.sources.base import FirmwareSource
from jayspray.sources.http import HttpClient
from jayspray.sources.samfrew import SamFrewSource
from jayspray.sources.samfw import SamFWSource
from jayspray.sources.sammobile import SamMobileSource


def configured_sources(config: AppConfig) -> tuple[FirmwareSource, ...]:
    """Build only explicitly enabled, fixed-origin discovery adapters."""
    client = HttpClient(config.discovery.http)
    available: dict[str, FirmwareSource] = {
        "samfrew": SamFrewSource(client),
        "samfw": SamFWSource(client),
        "sammobile": SamMobileSource(client),
    }
    return tuple(available[name] for name in config.discovery.sources)


__all__ = ["FirmwareSource", "configured_sources"]
