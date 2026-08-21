from __future__ import annotations

import json
from pathlib import Path

import pytest

from jayspray.db import Database
from jayspray.metadata_index import ExternalMetadataIndex


def test_index_builds_and_incrementally_reads_appended_models(
    database: Database, tmp_path: Path
) -> None:
    metadata = tmp_path / "metadata.json"
    initial = {
        "SM-S928U1/14/BUILD1": {"model": "SM-S928U1"},
        "SMA556E/14/BUILD2": {"model": "SMA556E", "padding": "x" * 6000},
    }
    metadata.write_text(json.dumps(initial, indent=4) + "\n", encoding="utf-8")
    index = ExternalMetadataIndex(database, metadata)

    first = index.refresh()
    assert first.rebuilt
    assert first.models_added == 2
    assert index.contains("sm-s928u1")
    assert not index.contains("SM-G999X")

    updated = dict(initial)
    updated["SM-G999X/15/BUILD3"] = {"model": "SM-G999X"}
    metadata.write_text(json.dumps(updated, indent=4) + "\n", encoding="utf-8")
    second = index.refresh()
    assert not second.rebuilt
    assert second.bytes_scanned < metadata.stat().st_size
    assert index.contains("SM-G999X")


def test_index_rebuilds_after_file_replacement(database: Database, tmp_path: Path) -> None:
    metadata = tmp_path / "metadata.json"
    metadata.write_text('{"old":{"model":"SM-S928U1"}}\n', encoding="utf-8")
    index = ExternalMetadataIndex(database, metadata)
    index.refresh()

    replacement = tmp_path / "replacement.json"
    replacement.write_text('{"new":{"model":"SM-A556E"}}\n', encoding="utf-8")
    replacement.replace(metadata)

    result = index.refresh()
    assert result.rebuilt
    assert not index.contains("SM-S928U1")
    assert index.contains("SM-A556E")


def test_index_rejects_symlinks(database: Database, tmp_path: Path) -> None:
    actual = tmp_path / "actual.json"
    actual.write_text("{}\n", encoding="utf-8")
    link = tmp_path / "metadata.json"
    link.symlink_to(actual)

    with pytest.raises(OSError):
        ExternalMetadataIndex(database, link).refresh()


def test_object_append_is_atomic_idempotent_and_refreshes_cache(
    database: Database, tmp_path: Path
) -> None:
    metadata = tmp_path / "metadata.json"
    metadata.write_text(
        '{"SM-S928U1/14/BUILD1":{"model":"SM-S928U1"}}\n', encoding="utf-8"
    )
    index = ExternalMetadataIndex(database, metadata)
    index.refresh()
    record = {
        "model": "SM-A556E",
        "android_version": "15",
        "build": "A556EXXU1",
        "rom_paths": ["/var/lib/jayspray/firmware.zip"],
    }

    assert index.append_catalog_entry("SM-A556E/15/A556EXXU1", record)
    assert not index.append_catalog_entry("SM-A556E/15/A556EXXU1", record)

    parsed = json.loads(metadata.read_text(encoding="utf-8"))
    assert parsed["SM-A556E/15/A556EXXU1"] == record
    assert index.contains("SM-A556E")


def test_append_creates_an_empty_default_catalog(database: Database, tmp_path: Path) -> None:
    metadata = tmp_path / "catalog" / "metadata.json"
    index = ExternalMetadataIndex(database, metadata, create_if_missing=True)
    index.refresh()

    assert json.loads(metadata.read_text(encoding="utf-8")) == {}


def test_million_line_catalog_is_cached_and_atomically_extended(
    database: Database, tmp_path: Path
) -> None:
    metadata = tmp_path / "metadata.json"
    metadata.write_text(
        "{\n" + ("\n" * 1_000_000) + '"old":{"model":"SM-S928U1"}\n}\n',
        encoding="utf-8",
    )
    index = ExternalMetadataIndex(database, metadata)

    stats = index.refresh()
    assert stats.models_added == 1
    assert index.append_catalog_entry(
        "SM-A556E/15/A556EXXU1", {"model": "SM-A556E"}
    )
    assert index.contains("SM-A556E")
    assert len(json.loads(metadata.read_text(encoding="utf-8"))) == 2


@pytest.mark.parametrize(
    "body",
    [
        '[{"model":"SM-S928U1"}]\n',
        '{"broken":{"model":"SM-S928U1"},}\n',
        '{"broken"}\n',
    ],
)
def test_append_rejects_invalid_root_without_modifying_file(
    database: Database, tmp_path: Path, body: str
) -> None:
    metadata = tmp_path / "metadata.json"
    metadata.write_text(body, encoding="utf-8")
    index = ExternalMetadataIndex(database, metadata)
    original = metadata.read_bytes()

    with pytest.raises(ValueError):
        index.append_catalog_entry(
            "SM-A556E/15/A556EXXU1", {"model": "SM-A556E"}
        )
    assert metadata.read_bytes() == original
