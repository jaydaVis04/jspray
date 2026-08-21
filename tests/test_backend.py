from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from jayspray.backend.samloader import BackendError, SamloaderBackend
from jayspray.config import DownloadConfig


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


def test_downloader_receives_only_allowlisted_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    binary = executable(tmp_path)
    captured: dict[str, str] = {}
    monkeypatch.setenv("UNRELATED_PRIVATE_VALUE", "must-not-leak")

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured.update(kwargs["env"])  # type: ignore[arg-type]
        return subprocess.CompletedProcess(
            args,
            0,
            stdout="A556EXXS1AXA1/A556EOXM1AXA1/A556EXXS1AXA1\n",
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    SamloaderBackend(DownloadConfig(samloader_executable=os.fspath(binary))).history(
        "SM-A556E", "EUX"
    )
    assert "UNRELATED_PRIVATE_VALUE" not in captured
    assert captured["LC_ALL"] == "C"


def test_rejects_writable_or_digest_mismatched_downloader(tmp_path: Path) -> None:
    binary = executable(tmp_path)
    binary.chmod(0o777)
    with pytest.raises(BackendError, match="not group/world writable"):
        SamloaderBackend(DownloadConfig(samloader_executable=os.fspath(binary)))
    binary.chmod(0o755)
    with pytest.raises(BackendError, match="SHA-256 does not match"):
        SamloaderBackend(
            DownloadConfig(samloader_executable=os.fspath(binary), samloader_sha256="0" * 64)
        )
