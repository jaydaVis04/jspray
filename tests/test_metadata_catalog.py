from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from jayspray.metadata_catalog import MetadataCatalogError, build_catalog_entry

EXPECTED_FIELDS = {
    "abi",
    "android_version",
    "build",
    "libc_md5",
    "model",
    "rom_path",
    "rom_paths",
    "vndk",
    "com_android_runtime_missing",
    "vndk_missing",
    "sqlite_source",
    "linker_source",
    "sqlite_md5",
    "linker_md5",
    "libril_source",
    "modem_missing",
    "vendor_sdk",
    "libril_sem_missing",
    "date",
    "build_security_patch",
    "vendor_security_patch",
    "hardware_egl",
    "hardware_chipname",
    "vendor_rild_libpath",
    "libsec_ril_source",
    "libsec_ril_md5",
    "framework_jar_source",
    "framework_jar_md5",
}


def _release(android_version: str | None = "14") -> dict[str, str | None]:
    return {
        "model": "SM-S928U1",
        "ap_version": "S928U1UES4AXH1",
        "android_version": android_version,
        "build_date": None,
    }


def _write(root: Path, relative: str, data: bytes) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def test_example_metadata_is_valid_and_documents_exact_schema() -> None:
    root = Path(__file__).resolve().parents[1]
    parsed = json.loads((root / "example_metadata.json").read_text(encoding="utf-8"))
    assert len(parsed) == 1
    record = next(iter(parsed.values()))
    assert set(record) == EXPECTED_FIELDS


def test_catalog_entry_derives_properties_sources_hashes_and_missing_flags(
    tmp_path: Path,
) -> None:
    extraction = tmp_path / "extracted"
    _write(
        extraction,
        "partitions/system/system/build.prop",
        b"\n".join(
            (
                b"ro.build.version.release=14",
                b"ro.product.cpu.abi=arm64-v8a",
                b"ro.vndk.version=34",
                b"ro.build.date.utc=1722384000",
                b"ro.build.version.security_patch=2024-08-01",
            )
        ),
    )
    _write(
        extraction,
        "partitions/vendor/build.prop",
        b"\n".join(
            (
                b"ro.vendor.build.version.sdk=34",
                b"ro.vendor.build.security_patch=2024-08-01",
                b"ro.hardware.egl=adreno",
                b"ro.hardware.chipname=example-chip",
                b"ro.vendor.rild.libpath=/vendor/lib64/libsec-ril.so",
            )
        ),
    )
    libc = _write(extraction, "partitions/system/system/lib64/libc.so", b"libc")
    _write(extraction, "partitions/system/system/lib64/libsqlite.so", b"sqlite")
    _write(extraction, "partitions/system/system/bin/linker64", b"linker")
    _write(extraction, "partitions/vendor/lib64/libril.so", b"ril")
    _write(extraction, "partitions/vendor/lib64/libsec-ril.so", b"sec-ril")
    _write(extraction, "partitions/system/system/framework/framework.jar", b"framework")
    _write(extraction, "partitions/system/apex/com.android.runtime.apex", b"runtime")
    _write(extraction, "CP_TEST.tar.md5", b"modem")
    _write(extraction, "partitions/vendor/lib64/libril-sem.so", b"sem")
    firmware = tmp_path / "firmware.zip"
    firmware.write_bytes(b"zip")

    result = build_catalog_entry(extraction, _release(), firmware)

    assert result.key == "SM-S928U1/14/S928U1UES4AXH1"
    assert set(result.record) == EXPECTED_FIELDS
    assert result.record["abi"] == "arm64-v8a"
    assert result.record["vndk"] == "34"
    assert result.record["vendor_sdk"] == "34"
    assert result.record["libc_md5"] == hashlib.md5(
        libc.read_bytes(), usedforsecurity=False
    ).hexdigest()
    assert result.record["framework_jar_source"].endswith("framework/framework.jar")
    assert not result.record["com_android_runtime_missing"]
    assert not result.record["modem_missing"]
    assert not result.record["libril_sem_missing"]


def test_catalog_entry_uses_nulls_and_explicit_missing_flags(tmp_path: Path) -> None:
    extraction = tmp_path / "extracted"
    extraction.mkdir()
    firmware = tmp_path / "firmware.zip"
    firmware.write_bytes(b"zip")

    result = build_catalog_entry(extraction, _release(), firmware)

    assert result.record["abi"] is None
    assert result.record["sqlite_source"] is None
    assert result.record["vndk_missing"]
    assert result.record["com_android_runtime_missing"]
    assert result.record["modem_missing"]


def test_catalog_entry_requires_evidence_for_android_version(tmp_path: Path) -> None:
    extraction = tmp_path / "extracted"
    extraction.mkdir()
    firmware = tmp_path / "firmware.zip"
    firmware.write_bytes(b"zip")

    with pytest.raises(MetadataCatalogError, match="Android version is unavailable"):
        build_catalog_entry(extraction, _release(android_version=None), firmware)
