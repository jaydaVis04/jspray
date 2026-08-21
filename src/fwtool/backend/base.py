from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path


class SamsungBackend(ABC):
    @abstractmethod
    def history(self, model: str, sales_csc: str) -> tuple[str, ...]:
        raise NotImplementedError

    @abstractmethod
    def download(
        self,
        model: str,
        sales_csc: str,
        full_version: str,
        output: Path,
    ) -> None:
        raise NotImplementedError

    @property
    @abstractmethod
    def supports_cross_process_resume(self) -> bool:
        raise NotImplementedError
