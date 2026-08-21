from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zipfile import ZIP_STORED, ZipFile

from jayspray.backend.base import SamsungBackend
from jayspray.config import AppConfig, MetadataConfig
from jayspray.db import Database
from jayspray.models import TargetObservation
from jayspray.orchestrator import discover, download_release, extract_release, probe
from jayspray.sources.base import FirmwareSource, SourcePage

PDA = "S928U1UES4AXH1"
XAA_VERSION = f"{PDA}/S928U1OYM4AXH1/{PDA}/{PDA}"
EUX_VERSION = f"{PDA}/S928U1OXM4AXH2/{PDA}/{PDA}"


class FakeBackend(SamsungBackend):
    def __init__(self) -> None:
        self.download_calls: list[tuple[str, str, str]] = []

    @property
    def supports_cross_process_resume(self) -> bool:
        return False

    def history(self, model: str, sales_csc: str) -> tuple[str, ...]:
        del model
        return {"XAA": (XAA_VERSION,), "EUX": (EUX_VERSION,)}[sales_csc]

    def download(self, model: str, sales_csc: str, full_version: str, output: Path) -> None:
        self.download_calls.append((model, sales_csc, full_version))
        output.parent.mkdir(parents=True, exist_ok=True)
        with ZipFile(output, "w", compression=ZIP_STORED) as archive:
            archive.writestr("AP_TEST.tar.md5", b"AP payload")
            archive.writestr("BL_TEST.tar.md5", b"BL payload")


def target(source: str, csc: str, *, age_days: int = 0) -> TargetObservation:
    observed = datetime.now(UTC) - timedelta(days=age_days)
    return TargetObservation(
        source=source,
        source_record_key=f"SM-S928U1:{csc}",
        source_url=f"https://{source}.example/latest",
        detail_url=None,
        model="SM-S928U1",
        sales_csc=csc,
        source_updated_date=observed.date().isoformat(),
        observed_at=observed,
    )


class FakeSource(FirmwareSource):
    name = "fixture"

    def fetch_page(self, page: int = 0) -> SourcePage:
        assert page == 0
        return SourcePage((target(self.name, "XAA"), target(self.name, "EUX")))


class IndependentFakeSource(FirmwareSource):
    name = "independent"

    def fetch_page(self, page: int = 0) -> SourcePage:
        assert page == 0
        return SourcePage((target(self.name, "XAA"),))


class OldSource(FirmwareSource):
    name = "old"

    def fetch_page(self, page: int = 0) -> SourcePage:
        assert page == 0
        return SourcePage((target(self.name, "XAA", age_days=22),))


def test_discovery_deduplicates_model_region_and_keeps_regions_separate(
    database: Database, app_config: AppConfig
) -> None:
    result = discover(database, app_config, (FakeSource(), IndependentFakeSource()))
    assert result.candidates == 3
    assert result.new_targets == 2
    assert len(database.list_targets()) == 2
    xaa = database.search_targets("SM-S928U1", csc="XAA")[0]
    assert database.target_sources(xaa["id"]) == ["fixture", "independent"]
    assert database.list_releases() == []


def test_discovery_rejects_targets_older_than_three_weeks(
    database: Database, app_config: AppConfig
) -> None:
    result = discover(database, app_config, (OldSource(),))
    assert result.candidates == 0
    assert result.filtered_old == 1
    assert database.list_targets() == []


def test_discovery_skips_every_region_when_model_is_in_external_metadata(
    database: Database, app_config: AppConfig, tmp_path: Path
) -> None:
    metadata = tmp_path / "metadata.json"
    metadata.write_text('{"model":"SM-S928U1"}\n', encoding="utf-8")
    configured = replace(
        app_config,
        metadata=MetadataConfig(path=metadata, append_completed=False),
    )

    result = discover(database, configured, (FakeSource(),))

    assert result.candidates == 0
    assert result.filtered_existing == 2
    assert database.list_targets() == []


def test_dry_run_does_not_persist(database: Database, app_config: AppConfig) -> None:
    result = discover(database, app_config, (FakeSource(),), dry_run=True)
    assert result.new_targets == 2
    assert database.list_targets() == []
    assert database.connection.execute("SELECT count(*) FROM run").fetchone()[0] == 0


def test_probe_uses_only_model_region_and_regions_get_distinct_releases(
    database: Database, app_config: AppConfig
) -> None:
    discover(database, app_config, (FakeSource(),))
    targets = database.list_targets()
    results = probe(
        database,
        FakeBackend(),
        first=2,
        target_ids=[str(row["id"]) for row in targets],
    )
    assert all(item.resolvable for item in results)
    assert {(item.model, item.sales_csc) for item in results} == {
        ("SM-S928U1", "XAA"),
        ("SM-S928U1", "EUX"),
    }
    assert len(database.list_releases()) == 2


def test_download_is_idempotent_then_extracts(
    database: Database, app_config: AppConfig
) -> None:
    backend = FakeBackend()
    discover(database, app_config, (FakeSource(),))
    target_row = database.search_targets(csc="XAA")[0]
    resolved = probe(
        database, backend, first=1, target_ids=[str(target_row["id"])]
    )[0]
    firmware = download_release(database, app_config, backend, resolved.release_id)
    assert firmware.name == "firmware.zip"
    assert database.model_has_verified_artifact("SM-S928U1")
    assert not database.model_has_verified_artifact(
        "SM-S928U1", exclude_release_id=resolved.release_id
    )
    assert backend.download_calls == [("SM-S928U1", "XAA", XAA_VERSION)]
    assert download_release(database, app_config, backend, resolved.release_id) == firmware
    assert len(backend.download_calls) == 1
    manifest = extract_release(database, app_config, resolved.release_id)
    assert manifest.is_file()
    assert database.get_release(resolved.release_id)["state"] == "EXTRACTED"


def test_completed_zip_is_reconciled_after_database_commit_interruption(
    database: Database, app_config: AppConfig
) -> None:
    backend = FakeBackend()
    discover(database, app_config, (FakeSource(),))
    target_row = database.search_targets(csc="XAA")[0]
    resolved = probe(
        database, backend, first=1, target_ids=[str(target_row["id"])]
    )[0]
    release = database.get_release(resolved.release_id)
    final_path = (
        app_config.paths.downloads
        / release["model"]
        / release["ap_version"]
        / release["id"]
        / "firmware.zip"
    )
    final_path.parent.mkdir(parents=True)
    with ZipFile(final_path, "w", compression=ZIP_STORED) as archive:
        archive.writestr("AP_RECOVERED.tar.md5", b"complete")
    assert download_release(database, app_config, backend, release["id"]) == final_path
    assert backend.download_calls == []
    assert database.get_release(release["id"])["state"] == "DECRYPTED"
