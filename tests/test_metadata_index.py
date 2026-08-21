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
    metadata.write_text(
        '{"model":"SM-S928U1","region":"XAA"}\n'
        '{"device":"SMA556E"}\n',
        encoding="utf-8",
    )
    index = ExternalMetadataIndex(database, metadata)

    first = index.refresh()
    assert first.rebuilt
    assert first.models_added == 2
    assert index.contains("sm-s928u1")
    assert not index.contains("SM-G999X")

    with metadata.open("a", encoding="utf-8") as handle:
        handle.write('{"model":"SM-G999X"}\n')
    second = index.refresh()
    assert not second.rebuilt
    assert second.bytes_scanned < metadata.stat().st_size
    assert index.contains("SM-G999X")


def test_index_rebuilds_after_file_replacement(database: Database, tmp_path: Path) -> None:
    metadata = tmp_path / "metadata.json"
    metadata.write_text('{"model":"SM-S928U1"}\n', encoding="utf-8")
    index = ExternalMetadataIndex(database, metadata)
    index.refresh()

    replacement = tmp_path / "replacement.json"
    replacement.write_text('{"model":"SM-A556E"}\n', encoding="utf-8")
    replacement.replace(metadata)

    result = index.refresh()
    assert result.rebuilt
    assert not index.contains("SM-S928U1")
    assert index.contains("SM-A556E")


def test_index_rejects_symlinks(database: Database, tmp_path: Path) -> None:
    actual = tmp_path / "actual.json"
    actual.write_text('{"model":"SM-S928U1"}\n', encoding="utf-8")
    link = tmp_path / "metadata.json"
    link.symlink_to(actual)

    with pytest.raises(OSError):
        ExternalMetadataIndex(database, link).refresh()


def test_jsonl_append_is_explicit_and_refreshes_cache(
    database: Database, tmp_path: Path
) -> None:
    metadata = tmp_path / "metadata.json"
    metadata.write_text('{"model":"SM-S928U1"}\n', encoding="utf-8")
    index = ExternalMetadataIndex(database, metadata)
    index.refresh()

    index.append_completed(
        {
            "model": "SM-A556E",
            "region": "INS",
            "full_version": "A556EXXU1/A556EODM1/A556EXXU1/A556EODM1",
            "firmware_release_id": "release-id",
            "artifact": "/var/lib/jayspray/firmware.zip",
            "sha256": "a" * 64,
            "completed_at": "2026-08-21T00:00:00+00:00",
        }
    )

    records = [json.loads(line) for line in metadata.read_text().splitlines()]
    assert records[-1]["model"] == "SM-A556E"
    assert records[-1]["source"] == "jayspray"
    assert index.contains("SM-A556E")


def test_append_rejects_top_level_json_array(database: Database, tmp_path: Path) -> None:
    metadata = tmp_path / "metadata.json"
    metadata.write_text('[{"model":"SM-S928U1"}]\n', encoding="utf-8")
    index = ExternalMetadataIndex(database, metadata)
    index.refresh()

    with pytest.raises(ValueError, match="JSON Lines"):
        index.append_completed(
            {
                "model": "SM-A556E",
                "region": "INS",
                "full_version": "A/B/C/D",
                "firmware_release_id": "release-id",
                "artifact": str(tmp_path / "firmware.zip"),
                "sha256": "b" * 64,
                "completed_at": "2026-08-21T00:00:00+00:00",
            }
        )
