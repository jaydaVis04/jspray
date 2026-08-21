from __future__ import annotations

import sqlite3

import pytest

from jayspray.db import Database
from jayspray.models import FirmwareObservation, ReleaseState, TargetObservation


def make_observation(csc: str, csc_component: str) -> FirmwareObservation:
    pda = "S928U1UES4AXH1"
    full = f"{pda}/{csc_component}/{pda}/{pda}"
    return FirmwareObservation(
        source="samsung_fus",
        source_record_key=f"SM-S928U1:{csc}:{full}",
        source_url="samsung-fus:SmartHistory",
        detail_url=None,
        model="SM-S928U1",
        sales_csc=csc,
        ap_version=pda,
        csc_version=csc_component,
        cp_version=pda,
        data_version=pda,
        full_version=full,
    )


def test_same_pda_in_two_regions_is_two_official_releases(database: Database) -> None:
    first = database.upsert_observation(make_observation("XAA", "S928U1OYM4AXH1"))
    second = database.upsert_observation(make_observation("EUX", "S928U1OXM4AXH2"))
    assert first.release_id != second.release_id
    assert first.outcome == "new_release"
    assert second.outcome == "new_release"
    assert second.source_count == 1
    rows = database.list_releases()
    assert len(rows) == 2
    assert {row["sales_csc"] for row in rows} == {"XAA", "EUX"}
    routes = database.route_observations(first.release_id)
    assert [route["csc"] for route in routes] == ["XAA"]


def test_repeated_observation_is_idempotent(database: Database) -> None:
    item = make_observation("XAA", "S928U1OYM4AXH1")
    first = database.upsert_observation(item)
    second = database.upsert_observation(item)
    assert first.release_id == second.release_id
    assert second.outcome == "matched_observation"
    count = database.connection.execute("SELECT count(*) FROM source_observation").fetchone()[0]
    assert count == 1


def test_database_enforces_canonical_identity(database: Database) -> None:
    first = database.upsert_observation(make_observation("XAA", "S928U1OYM4AXH1"))
    row = database.get_release(first.release_id)
    assert row is not None
    with database.transaction(), pytest.raises(sqlite3.IntegrityError):
        database.connection.execute(
            """INSERT INTO firmware_release(
                 id, weak_key, strong_key, model, sales_csc, ap_version, state,
                 first_discovered_at, last_observed_at, state_updated_at, created_at, updated_at
               ) VALUES ('duplicate', ?, ?, 'SM-S928U1', 'XAA', 'S928U1UES4AXH1',
                         'DISCOVERED', 'x', 'x', 'x', 'x', 'x')""",
            (row["weak_key"], row["strong_key"]),
        )


def test_state_survives_restart(app_config) -> None:  # type: ignore[no-untyped-def]
    with Database(app_config.paths.database) as first_db:
        first_db.migrate()
        release = first_db.upsert_observation(make_observation("XAA", "S928U1OYM4AXH1"))
        first_db.set_state(release.release_id, ReleaseState.RESOLVED)
    with Database(app_config.paths.database) as second_db:
        second_db.migrate()
        assert second_db.get_release(release.release_id)["state"] == "RESOLVED"


def test_readonly_snapshot_does_not_create_missing_database(app_config) -> None:  # type: ignore[no-untyped-def]
    assert not app_config.paths.database.exists()
    with Database.readonly_snapshot(app_config.paths.database) as snapshot:
        assert snapshot.list_releases() == []
    assert not app_config.paths.database.exists()


def test_search_uses_literal_patterns_and_bounded_limit(database: Database) -> None:
    release = database.upsert_observation(make_observation("XAA", "S928U1OYM4AXH1"))
    assert [row["id"] for row in database.search("SM-S928", csc="xaa", pda="S928U1")] == [
        release.release_id
    ]
    assert database.search("%") == []
    with pytest.raises(ValueError, match="between 1 and 10000"):
        database.search(limit=-1)


def test_source_checkpoint_tracks_success_and_redacts_failure(database: Database) -> None:
    database.update_source_checkpoint("samfrew", successful=True, latest_record_key="record-1")
    database.update_source_checkpoint(
        "samfrew", successful=False, error="Authorization: Bearer private-value"
    )
    source = database.status_summary()["sources"][0]
    assert source["latest_record_key"] == "record-1"
    assert source["last_success_at"] is not None
    assert "private-value" not in source["last_error"]


def test_legacy_watch_target_table_is_removed(database: Database) -> None:
    row = database.connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'watch_target'"
    ).fetchone()
    assert row is None


def test_model_region_targets_merge_source_provenance(database: Database) -> None:
    base = dict(
        source_record_key="SM-S928U1:XAA",
        detail_url=None,
        model="SM-S928U1",
        sales_csc="XAA",
        android_version="14",
        source_updated_date="2026-08-21",
    )
    first = database.upsert_target_observation(
        TargetObservation(
            source="samfrew", source_url="https://samfrew.example", **base
        )
    )
    second = database.upsert_target_observation(
        TargetObservation(
            source="sammobile", source_url="https://sammobile.example", **base
        )
    )

    assert first.target_id == second.target_id
    assert second.source_count == 2
    assert database.target_sources(first.target_id) == ["samfrew", "sammobile"]
    targets = database.search_targets("SM-S928U1", csc="xaa")
    assert len(targets) == 1
    assert targets[0]["android_version"] == "14"
