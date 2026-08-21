from __future__ import annotations

from pathlib import Path

import pytest

from jayspray.config import AppConfig, DownloadConfig, PathsConfig
from jayspray.db import Database


@pytest.fixture
def app_config(tmp_path: Path) -> AppConfig:
    return AppConfig(
        paths=PathsConfig(
            database=tmp_path / "database" / "firmware.db",
            downloads=tmp_path / "downloads",
            extracted=tmp_path / "extracted",
            cache=tmp_path / "cache",
            state=tmp_path / "state",
        ),
        download=DownloadConfig(minimum_free_bytes=1),
    )


@pytest.fixture
def database(app_config: AppConfig) -> Database:
    db = Database(app_config.paths.database)
    db.migrate()
    yield db
    db.close()
