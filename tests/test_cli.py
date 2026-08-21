from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from jayspray import cli
from jayspray.backend.base import SamsungBackend
from jayspray.config import AppConfig
from jayspray.db import Database

PDA = "S928U1UES4AXH1"


class DryRunBackend(SamsungBackend):
    @property
    def supports_cross_process_resume(self) -> bool:
        return False

    def history(self, model: str, sales_csc: str) -> tuple[str, ...]:
        del model
        csc_component = {"XAA": "S928U1OYM4AXH1", "EUX": "S928U1OXM4AXH2"}[sales_csc]
        return (f"{PDA}/{csc_component}/{PDA}/{PDA}",)

    def download(self, model: str, sales_csc: str, full_version: str, output: Path) -> None:
        raise AssertionError("dry run must not download")


def test_sync_dry_run_explains_queue_duplicate_and_download_skip(
    database: Database,
    app_config: AppConfig,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(cli, "SamloaderBackend", lambda _config: DryRunBackend())
    args = argparse.Namespace(command="sync", limit=None, dry_run=True)

    assert cli._run_discover(database, app_config, args) == 0
    output = capsys.readouterr().out
    assert "WOULD QUEUE SM-S928U1 XAA" in output
    assert "SKIP DUPLICATE SM-S928U1 EUX" in output
    assert "reason=same_model_and_pda" in output
    assert "reason=automatic_download_disabled" in output
    assert database.list_releases() == []
