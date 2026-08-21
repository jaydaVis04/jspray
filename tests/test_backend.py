from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from fwtool.backend.samloader import BackendError, SamloaderBackend
from fwtool.config import DownloadConfig


def executable(tmp_path: Path) -> Path:
    path = tmp_path / "samloader"
    path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    path.chmod(0o755)
    return path


def test_history_uses_only_check_update_argument_vector(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    binary = executable(tmp_path)
    calls: list[list[str]] = []

    def fake_run(args: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        return subprocess.CompletedProcess(
            args,
            0,
            stdout=(
                "Latest Stable Version:\n"
                "S928U1UES4AXH1/S928U1OYM4AXH1/S928U1UES4AXH1/S928U1UES4AXH1\n"
            ),
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    backend = SamloaderBackend(DownloadConfig(samloader_executable=str(binary)))
    versions = backend.history("SM-S928U1", "XAA")
    assert versions[0].startswith("S928U1UES4AXH1/")
    assert calls == [
        [str(binary.resolve()), "check-update", "--all", "-m", "SM-S928U1", "-r", "XAA"]
    ]


def test_sensitive_backend_error_lines_are_redacted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    binary = executable(tmp_path)

    def fake_run(args: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args, 1, stdout="", stderr="Authorization: secret-value")

    monkeypatch.setattr(subprocess, "run", fake_run)
    backend = SamloaderBackend(DownloadConfig(samloader_executable=os.fspath(binary)))
    with pytest.raises(BackendError) as caught:
        backend.history("SM-S928U1", "XAA")
    assert "secret-value" not in str(caught.value)
    assert "REDACTED" in str(caught.value)
