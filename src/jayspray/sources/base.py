from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from jayspray.models import FirmwareObservation


class SourceError(RuntimeError):
    """An isolated discovery-source failure."""


class ParserError(SourceError):
    """The source responded, but its newest-release feed could not be parsed."""


@dataclass(frozen=True, slots=True)
class SourcePage:
    observations: tuple[FirmwareObservation, ...]
    next_page: int | None = None


class FirmwareSource(ABC):
    name: str

    @abstractmethod
    def fetch_page(self, page: int = 0) -> SourcePage:
        """Fetch one newest-first page. Page zero is always the newest page."""
