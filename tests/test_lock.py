from __future__ import annotations

from pathlib import Path

import pytest

from jayspray.lock import LockUnavailableError, ProcessLock


def test_process_lock_rejects_symlink(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.write_text("do not overwrite", encoding="utf-8")
    lock_path = tmp_path / "jayspray.lock"
    lock_path.symlink_to(target)

    with pytest.raises(LockUnavailableError, match="safe regular file"), ProcessLock(lock_path):
        pass
    assert target.read_text(encoding="utf-8") == "do not overwrite"
