from __future__ import annotations

import argparse
from datetime import UTC, datetime
from pathlib import Path

import pytest

from jayspray import cli
from jayspray.backend.base import SamsungBackend
from jayspray.config import AppConfig
from jayspray.db import Database
from jayspray.models import TargetObservation
from jayspray.sources.base import FirmwareSource, SourcePage


class DryRunBackend(SamsungBackend):
    @property
    def supports_cross_process_resume(self) -> bool:
        return False

    def history(self, model: str, sales_csc: str) -> tuple[str, ...]:
        del model
        csc_component = {"XAA": "S928U1OYM4AXH1", "EUX": "S928U1OXM4AXH2"}[sales_csc]
        pda = "S928U1UES4AXH1"
        return (f"{pda}/{csc_component}/{pda}/{pda}",)

    def download(self, model: str, sales_csc: str, full_version: str, output: Path) -> None:
        raise AssertionError("dry run must not download")


class DryRunSource(FirmwareSource):
    name = "fixture"

    def fetch_page(self, page: int = 0) -> SourcePage:
        del page
        return SourcePage(
            tuple(
                TargetObservation(
                    source="fixture",
                    source_record_key=csc,
                    source_url="https://example.invalid/latest",
                    detail_url=None,
                    model="SM-S928U1",
                    sales_csc=csc,
                    source_updated_date=datetime.now(UTC).date().isoformat(),
                )
                for csc in ("XAA", "EUX")
            )
        )


def test_sync_dry_run_selects_only_one_region_per_model(
    database: Database,
    app_config: AppConfig,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(cli, "configured_sources", lambda _config: (DryRunSource(),))
    args = argparse.Namespace(command="sync", limit=None, dry_run=True)

    assert cli._run_discover(database, app_config, args) == 0
    output = capsys.readouterr().out
    assert "WOULD RESOLVE LATEST SM-S928U1 XAA" in output
    assert "SKIP TARGET SM-S928U1 EUX reason=model_already_selected" in output
    assert "reason=automatic_download_disabled" in output
    assert database.list_releases() == []
