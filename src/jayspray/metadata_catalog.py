from __future__ import annotations

import hashlib
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jayspray.identity import normalized_model, normalized_version

MAX_PROPERTY_BYTES = 2 * 1024 * 1024
MAX_ANALYZED_FILES = 1_000_000


class MetadataCatalogError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class MetadataCatalogEntry:
    key: str
    record: dict[str, Any]


def _read_regular_file(path: Path, *, maximum_bytes: int | None = None) -> bytes:
    flags = os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        details = os.fstat(descriptor)
        if not stat.S_ISREG(details.st_mode):
            raise MetadataCatalogError(f"metadata input is not a regular file: {path}")
        if maximum_bytes is not None and details.st_size > maximum_bytes:
            raise MetadataCatalogError(f"metadata input exceeds its size limit: {path}")
        chunks: list[bytes] = []
        while chunk := os.read(descriptor, 1024 * 1024):
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _parse_properties(path: Path) -> dict[str, str]:
    try:
        text = _read_regular_file(path, maximum_bytes=MAX_PROPERTY_BYTES).decode(
            "utf-8", errors="replace"
        )
    except OSError:
        return {}
    properties: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key and key not in properties:
            properties[key] = value.strip()
    return properties


def _md5_compatibility(path: Path) -> str:
    # The external schema requires MD5 as a compatibility identifier, not as a
    # security guarantee. Download integrity continues to use SHA-256.
    digest = hashlib.md5(usedforsecurity=False)
    flags = os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        details = os.fstat(descriptor)
        if not stat.S_ISREG(details.st_mode):
            raise MetadataCatalogError(f"hash input is not a regular file: {path}")
        while chunk := os.read(descriptor, 1024 * 1024):
            digest.update(chunk)
    finally:
        os.close(descriptor)
    return digest.hexdigest()


def _inventory(root: Path) -> list[tuple[str, Path]]:
    files: list[tuple[str, Path]] = []
    for directory, names, filenames in os.walk(root, followlinks=False):
        directory_path = Path(directory)
        names[:] = [
            name for name in names if not (directory_path / name).is_symlink()
        ]
        for filename in filenames:
            path = directory_path / filename
            if path.is_symlink():
                continue
            try:
                relative = path.relative_to(root).as_posix()
            except ValueError as exc:
                raise MetadataCatalogError("metadata inventory escaped extraction root") from exc
            files.append((relative, path))
            if len(files) > MAX_ANALYZED_FILES:
                raise MetadataCatalogError("extraction tree exceeds metadata file-count limit")
    return files


def _property_sets(files: list[tuple[str, Path]]) -> list[tuple[str, dict[str, str]]]:
    results = []
    for relative, path in files:
        if path.name.lower() in {"build.prop", "default.prop"}:
            results.append((relative.lower(), _parse_properties(path)))
    return results


def _merged_properties(
    sets: list[tuple[str, dict[str, str]]], *, vendor: bool
) -> dict[str, str]:
    matching = [item for item in sets if ("vendor" in item[0].split("/")) is vendor]
    matching.sort(key=lambda item: ("system" not in item[0].split("/"), item[0]))
    merged: dict[str, str] = {}
    for _, properties in matching:
        for key, value in properties.items():
            merged.setdefault(key, value)
    return merged


def _first(properties: dict[str, str], *keys: str) -> str | None:
    for key in keys:
        value = properties.get(key)
        if value:
            return value
    return None


def _candidate(
    files: list[tuple[str, Path]], names: set[str], *, prefer: str | None = None
) -> tuple[str, Path] | None:
    matches = [item for item in files if Path(item[0]).name.lower() in names]
    if not matches:
        return None
    matches.sort(key=lambda item: (prefer not in item[0].lower() if prefer else False, item[0]))
    return matches[0]


def _source_and_md5(candidate: tuple[str, Path] | None) -> tuple[str | None, str | None]:
    if candidate is None:
        return None, None
    relative, path = candidate
    return relative, _md5_compatibility(path)


def build_catalog_entry(
    extraction_root: Path,
    release: Any,
    firmware_zip: Path,
) -> MetadataCatalogEntry:
    """Derive the public metadata schema from verified extraction evidence."""
    if not extraction_root.is_dir() or extraction_root.is_symlink():
        raise MetadataCatalogError("metadata extraction root must be a real directory")
    model = normalized_model(str(release["model"]))
    build = normalized_version(str(release["ap_version"]))
    if build is None or "/" in build:
        raise MetadataCatalogError("metadata catalog requires a resolved AP build")

    files = _inventory(extraction_root)
    property_sets = _property_sets(files)
    system_properties = _merged_properties(property_sets, vendor=False)
    vendor_properties = _merged_properties(property_sets, vendor=True)

    release_android = release["android_version"]
    android_version = _first(system_properties, "ro.build.version.release") or (
        str(release_android) if release_android else None
    )
    if not android_version:
        raise MetadataCatalogError(
            "Android version is unavailable; retain index metadata or unpack system build.prop"
        )
    if "/" in android_version or any(char.isspace() for char in android_version):
        raise MetadataCatalogError("Android version cannot be used in a metadata key")

    abi = _first(
        system_properties,
        "ro.product.cpu.abi",
        "ro.system.product.cpu.abi",
        "ro.product.cpu.abilist",
        "ro.system.product.cpu.abilist",
    )
    if abi and "," in abi:
        abi = abi.split(",", 1)[0]
    vndk = _first(system_properties, "ro.vndk.version") or _first(
        vendor_properties, "ro.vndk.version"
    )
    vendor_sdk = _first(
        vendor_properties,
        "ro.vendor.build.version.sdk",
        "ro.build.version.sdk",
    )

    libc_source, libc_md5 = _source_and_md5(
        _candidate(files, {"libc.so"}, prefer="/lib64/")
    )
    del libc_source  # The requested schema stores only libc_md5.
    sqlite_source, sqlite_md5 = _source_and_md5(
        _candidate(files, {"libsqlite.so", "libsqlite3.so", "sqlite3"}, prefer="/lib64/")
    )
    linker_source, linker_md5 = _source_and_md5(
        _candidate(files, {"linker64", "linker"}, prefer="linker64")
    )
    libril_source, _ = _source_and_md5(
        _candidate(files, {"libril.so"}, prefer="/vendor/")
    )
    libsec_ril_source, libsec_ril_md5 = _source_and_md5(
        _candidate(
            files,
            {"libsec-ril.so", "libsec_ril.so", "libsec-ril-dsds.so"},
            prefer="/vendor/",
        )
    )
    framework_jar_source, framework_jar_md5 = _source_and_md5(
        _candidate(files, {"framework.jar"}, prefer="/system/")
    )

    lowered_paths = [relative.lower() for relative, _ in files]
    modem_present = any(
        Path(relative).name.startswith("cp_")
        or "modem" in Path(relative).name
        for relative in lowered_paths
    )
    runtime_present = any("com.android.runtime" in relative for relative in lowered_paths)
    libril_sem_present = any(
        "libril-sem" in relative or "libril_sem" in relative
        for relative in lowered_paths
    )
    archive_path = str(firmware_zip)
    record: dict[str, Any] = {
        "abi": abi,
        "android_version": android_version,
        "build": build,
        "libc_md5": libc_md5,
        "model": model,
        "rom_path": archive_path,
        "rom_paths": [archive_path],
        "vndk": vndk,
        "com_android_runtime_missing": not runtime_present,
        "vndk_missing": vndk is None,
        "sqlite_source": sqlite_source,
        "linker_source": linker_source,
        "sqlite_md5": sqlite_md5,
        "linker_md5": linker_md5,
        "libril_source": libril_source,
        "modem_missing": not modem_present,
        "vendor_sdk": vendor_sdk,
        "libril_sem_missing": not libril_sem_present,
        "date": _first(system_properties, "ro.build.date.utc")
        or (str(release["build_date"]) if release["build_date"] else None),
        "build_security_patch": _first(
            system_properties, "ro.build.version.security_patch"
        ),
        "vendor_security_patch": _first(
            vendor_properties, "ro.vendor.build.security_patch"
        ),
        "hardware_egl": _first(
            vendor_properties, "ro.hardware.egl", "ro.board.platform"
        ),
        "hardware_chipname": _first(
            vendor_properties, "ro.hardware.chipname", "ro.soc.model"
        ),
        "vendor_rild_libpath": _first(
            vendor_properties, "ro.vendor.rild.libpath", "rild.libpath"
        ),
        "libsec_ril_source": libsec_ril_source,
        "libsec_ril_md5": libsec_ril_md5,
        "framework_jar_source": framework_jar_source,
        "framework_jar_md5": framework_jar_md5,
    }
    return MetadataCatalogEntry(f"{model}/{android_version}/{build}", record)
