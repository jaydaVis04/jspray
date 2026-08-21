from __future__ import annotations

import fcntl
import os
import stat
from pathlib import Path
from types import TracebackType


class LockUnavailableError(RuntimeError):
    pass


class ProcessLock:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._fd: int | None = None

    def __enter__(self) -> ProcessLock:
        self.path.parent.mkdir(mode=0o750, parents=True, exist_ok=True)
        flags = os.O_RDWR | os.O_CREAT | os.O_CLOEXEC | os.O_NOFOLLOW
        try:
            self._fd = os.open(self.path, flags, 0o640)
        except OSError as exc:
            raise LockUnavailableError("jayspray lock path is not a safe regular file") from exc
        if not stat.S_ISREG(os.fstat(self._fd).st_mode):
            os.close(self._fd)
            self._fd = None
            raise LockUnavailableError("jayspray lock path is not a regular file")
        os.fchmod(self._fd, 0o640)
        try:
            fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            os.close(self._fd)
            self._fd = None
            raise LockUnavailableError(
                "another jayspray mutation process is already running"
            ) from exc
        os.ftruncate(self._fd, 0)
        os.write(self._fd, f"{os.getpid()}\n".encode())
        os.fsync(self._fd)
        return self

    def __exit__(
        self,
        _exc_type: type[BaseException] | None,
        _exc: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        if self._fd is not None:
            fcntl.flock(self._fd, fcntl.LOCK_UN)
            os.close(self._fd)
            self._fd = None
