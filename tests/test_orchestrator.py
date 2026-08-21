from __future__ import annotations

from pathlib import Path
from zipfile import ZIP_STORED, ZipFile

from jayspray.backend.base import SamsungBackend
from jayspray.config import AppConfig
from jayspray.db import Database
from jayspray.models import FirmwareObservation
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


class FakeSource(FirmwareSource):
    name = "fixture"

    def fetch_page(self, page: int = 0) -> SourcePage:
        assert page == 0
        return SourcePage(
            tuple(
                FirmwareObservation(
                    source="fixture",
                    source_record_key=csc,
                    source_url="https://example.invalid/latest",
                    detail_url=None,
                    model="SM-S928U1",
                    sales_csc=csc,
                    ap_version=PDA,
                    full_version=version,
                )
                for csc, version in (("XAA", XAA_VERSION), ("EUX", EUX_VERSION))
            )
        )


class IndependentFakeSource(FirmwareSource):
    name = "independent"

    def fetch_page(self, page: int = 0) -> SourcePage:
        assert page == 0
        return SourcePage(
            (
                FirmwareObservation(
                    source=self.name,
                    source_record_key="independent-record",
                    source_url="https://independent.example/latest",
                    detail_url=None,
                    model="SM-S928U1",
                    sales_csc="VZW",
                    ap_version=PDA,
                ),
            )
        )


def test_discovery_merges_same_pda_across_csc(database: Database, app_config: AppConfig) -> None:
    result = discover(database, app_config, (FakeSource(),))
    assert result.candidates == 2
    assert result.new_releases == 1
    assert result.matched_observations == 1
    assert len(database.list_releases()) == 1


def test_discovery_preserves_cross_source_agreement(
    database: Database, app_config: AppConfig
) -> None:
    discover(database, app_config, (FakeSource(), IndependentFakeSource()))
    release = database.list_releases()[0]
    assert database.sources_for_release(release["id"]) == ["fixture", "independent"]
    assert {route["csc"] for route in database.route_observations(release["id"])} == {
        "XAA",
        "EUX",
        "VZW",
    }


def test_dry_run_does_not_persist(database: Database, app_config: AppConfig) -> None:
    result = discover(database, app_config, (FakeSource(),), dry_run=True)
    assert result.new_releases == 1
    assert database.list_releases() == []
    assert database.connection.execute("SELECT count(*) FROM run").fetchone()[0] == 0


def test_download_uses_first_csc_once_then_extracts(
    database: Database, app_config: AppConfig
) -> None:
    backend = FakeBackend()
    discover(database, app_config, (FakeSource(),))
    release = database.list_releases()[0]
    firmware = download_release(database, app_config, backend, release["id"])
    assert firmware.name == "firmware.zip"
    assert backend.download_calls == [("SM-S928U1", "XAA", XAA_VERSION)]
    second = download_release(database, app_config, backend, release["id"])
    assert second == firmware
    assert len(backend.download_calls) == 1
    manifest = extract_release(database, app_config, release["id"])
    assert manifest.is_file()
    assert database.get_release(release["id"])["state"] == "EXTRACTED"
    assert extract_release(database, app_config, release["id"]) == manifest


def test_completed_zip_is_reconciled_after_database_commit_interruption(
    database: Database, app_config: AppConfig
) -> None:
    backend = FakeBackend()
    discover(database, app_config, (FakeSource(),))
    release = database.list_releases()[0]
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


def test_probe_resolves_pda_using_an_observed_csc_route(
    database: Database, app_config: AppConfig
) -> None:
    source = FakeSource()
    discover(database, app_config, (source,))
    release = database.list_releases()[0]
    database.connection.execute(
        "UPDATE firmware_release SET full_version = NULL, csc_version = NULL, cp_version = NULL "
        "WHERE id = ?",
        (release["id"],),
    )
    database.connection.commit()
    result = probe(database, FakeBackend(), first=1)
    assert result[0].resolvable
    assert database.get_release(release["id"])["full_version"] == XAA_VERSION
