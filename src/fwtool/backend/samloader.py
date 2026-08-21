from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

from fwtool.backend.base import SamsungBackend
from fwtool.config import DownloadConfig
from fwtool.identity import normalized_csc, normalized_model, normalized_version

HISTORY_VERSION_RE = re.compile(r"[A-Z0-9._+-]{4,}(?:/[A-Z0-9._+-]{4,}){2,3}")
SENSITIVE_DIAGNOSTIC_RE = re.compile(
    r"(?:authorization|cookie|nonce|password|secret|session|token)", re.IGNORECASE
)


class BackendError(RuntimeError):
    pass


class BackendUnavailableError(BackendError):
    pass


class SamloaderBackend(SamsungBackend):
    """Narrow, shell-free adapter for samloader-rs 2.x.

    Only the non-flashing `check-update` and `download` subcommands are present here.
    """

    def __init__(self, config: DownloadConfig) -> None:
        self.config = config
        executable = config.samloader_executable
        resolved = shutil.which(executable) if "/" not in executable else executable
        if not resolved:
            raise BackendUnavailableError(
                "samloader executable not found; install samloader-rs 2.x and configure its path"
            )
        path = Path(resolved)
        if not path.is_absolute() or not path.is_file() or not os.access(path, os.X_OK):
            raise BackendUnavailableError("configured samloader path is not an executable file")
        self.executable = path.resolve()

    @property
    def supports_cross_process_resume(self) -> bool:
        return False

    def _run(self, args: list[str]) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env.update({"LC_ALL": "C", "LANG": "C"})
        try:
            result = subprocess.run(  # noqa: S603
                [str(self.executable), *args],
                check=False,
                capture_output=True,
                text=True,
                timeout=self.config.command_timeout_seconds,
                env=env,
            )
        except subprocess.TimeoutExpired as exc:
            raise BackendError("samloader operation exceeded its configured timeout") from exc
        if result.returncode != 0:
            message = (result.stderr or result.stdout or "samloader failed").strip()
            safe_lines = [
                "[REDACTED BACKEND DIAGNOSTIC]" if SENSITIVE_DIAGNOSTIC_RE.search(line) else line
                for line in message.splitlines()
            ]
            safe_message = "\n".join(safe_lines)[:1000]
            raise BackendError(f"samloader exited {result.returncode}: {safe_message}")
        return result

    def history(self, model: str, sales_csc: str) -> tuple[str, ...]:
        safe_model = normalized_model(model)
        safe_csc = normalized_csc(sales_csc)
        result = self._run(["check-update", "--all", "-m", safe_model, "-r", safe_csc])
        versions: list[str] = []
        for match in HISTORY_VERSION_RE.finditer(f"{result.stdout}\n{result.stderr}"):
            version = normalized_version(match.group(0))
            if version and version not in versions:
                versions.append(version)
        if not versions:
            raise BackendError("samloader returned no parseable Samsung firmware history")
        return tuple(versions)

    def download(
        self,
        model: str,
        sales_csc: str,
        full_version: str,
        output: Path,
    ) -> None:
        safe_model = normalized_model(model)
        safe_csc = normalized_csc(sales_csc)
        safe_version = normalized_version(full_version)
        if safe_version is None or len(safe_version.split("/")) < 3:
            raise BackendError("an exact Samsung slash-delimited version is required")
        if not output.is_absolute():
            raise BackendError("download output path must be absolute")
        output.parent.mkdir(mode=0o750, parents=True, exist_ok=True)
        self._run(
            [
                "download",
                "-m",
                safe_model,
                "-r",
                safe_csc,
                "-v",
                safe_version,
                "-j",
                str(self.config.connections_per_file),
                "-o",
                str(output),
            ]
        )
        if not output.is_file() or output.stat().st_size == 0:
            raise BackendError("samloader reported success but produced no file")
