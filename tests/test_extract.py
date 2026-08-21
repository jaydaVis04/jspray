from __future__ import annotations

from pathlib import Path
from zipfile import ZipFile

import pytest

from jayspray.config import ExtractConfig
from jayspray.extract import ArchiveError, extract_firmware, verify_zip


def test_rejects_zip_traversal(tmp_path: Path) -> None:
    archive = tmp_path / "bad.zip"
    with ZipFile(archive, "w") as handle:
        handle.writestr("../escape", b"no")
    with pytest.raises(ArchiveError):
        verify_zip(archive, ExtractConfig())


def test_manifest_catalogs_components(tmp_path: Path) -> None:
    archive = tmp_path / "firmware.zip"
    with ZipFile(archive, "w") as handle:
        handle.writestr("AP_BUILD.tar.md5", b"ap")
        handle.writestr("HOME_CSC_BUILD.tar.md5", b"csc")
        handle.writestr("notes.txt", b"notes")
    manifest, entries = extract_firmware(
        archive,
        tmp_path / "output",
        ExtractConfig(),
        release_metadata={"id": "test"},
    )
    assert manifest.is_file()
    assert [entry.component for entry in entries] == ["AP", "HOME_CSC", None]
    assert all(entry.sha256 for entry in entries)
