from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import tempfile
import zipfile
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import IO

from fwtool.config import ExtractConfig


class ArchiveError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class FileManifestEntry:
    filename: str
    relative_path: str
    size: int
    sha256: str
    component: str | None
    nested_tar_md5: bool


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _component(name: str) -> str | None:
    upper = Path(name).name.upper()
    for prefix in ("HOME_CSC_", "USERDATA_", "AP_", "BL_", "CP_", "CSC_"):
        if upper.startswith(prefix):
            return prefix.rstrip("_")
    return None


def _safe_relative(name: str) -> PurePosixPath:
    if "\x00" in name or "\\" in name:
        raise ArchiveError(f"unsafe ZIP member name: {name!r}")
    path = PurePosixPath(name)
    if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} for part in path.parts):
        raise ArchiveError(f"unsafe ZIP member path: {name!r}")
    return path


def _validate_members(archive: zipfile.ZipFile, config: ExtractConfig) -> list[zipfile.ZipInfo]:
    members = archive.infolist()
    if len(members) > config.max_members:
        raise ArchiveError("ZIP member count exceeds configured limit")
    total = 0
    for member in members:
        _safe_relative(member.filename.rstrip("/"))
        mode = member.external_attr >> 16
        if stat.S_ISLNK(mode) or stat.S_ISCHR(mode) or stat.S_ISBLK(mode) or stat.S_ISFIFO(mode):
            raise ArchiveError(f"unsupported special ZIP member: {member.filename}")
        if member.file_size > config.max_member_bytes:
            raise ArchiveError(f"ZIP member exceeds configured size: {member.filename}")
        total += member.file_size
        if total > config.max_total_bytes:
            raise ArchiveError("ZIP expanded size exceeds configured limit")
        if member.file_size and member.compress_size == 0:
            raise ArchiveError(f"invalid compressed size for ZIP member: {member.filename}")
        if (
            member.compress_size
            and member.file_size / member.compress_size > config.max_compression_ratio
        ):
            raise ArchiveError(f"suspicious compression ratio for ZIP member: {member.filename}")
    return members


def verify_zip(path: Path, config: ExtractConfig) -> None:
    if not path.is_file():
        raise ArchiveError("firmware ZIP does not exist")
    try:
        with zipfile.ZipFile(path) as archive:
            _validate_members(archive, config)
            corrupt = archive.testzip()
            if corrupt is not None:
                raise ArchiveError(f"ZIP CRC verification failed at member: {corrupt}")
    except zipfile.BadZipFile as exc:
        raise ArchiveError("backend output is not a valid ZIP archive") from exc


def _copy_and_hash(source: IO[bytes], destination: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC
    fd = os.open(destination, flags, 0o640)
    try:
        with os.fdopen(fd, "wb") as output:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                output.write(chunk)
                digest.update(chunk)
                size += len(chunk)
            output.flush()
            os.fsync(output.fileno())
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    return size, digest.hexdigest()


def extract_firmware(
    archive_path: Path,
    destination: Path,
    config: ExtractConfig,
    *,
    release_metadata: dict[str, str | None],
) -> tuple[Path, list[FileManifestEntry]]:
    verify_zip(archive_path, config)
    destination.parent.mkdir(mode=0o750, parents=True, exist_ok=True)
    if destination.exists():
        raise ArchiveError(f"extraction destination already exists: {destination}")
    temp = Path(tempfile.mkdtemp(prefix=f".{destination.name}.", dir=destination.parent))
    entries: list[FileManifestEntry] = []
    try:
        with zipfile.ZipFile(archive_path) as archive:
            members = _validate_members(archive, config)
            for member in members:
                relative = _safe_relative(member.filename.rstrip("/"))
                target = temp.joinpath(*relative.parts)
                target.parent.mkdir(mode=0o750, parents=True, exist_ok=True)
                if member.is_dir():
                    target.mkdir(mode=0o750, exist_ok=True)
                    continue
                with archive.open(member, "r") as source:
                    size, digest = _copy_and_hash(source, target)
                if size != member.file_size:
                    raise ArchiveError(f"extracted size mismatch: {member.filename}")
                entries.append(
                    FileManifestEntry(
                        filename=target.name,
                        relative_path=relative.as_posix(),
                        size=size,
                        sha256=digest,
                        component=_component(target.name),
                        nested_tar_md5=target.name.lower().endswith(".tar.md5"),
                    )
                )
        manifest = {
            "schema_version": 1,
            "generated_at": datetime.now(UTC).isoformat(),
            "firmware": release_metadata,
            "source_archive": {
                "filename": archive_path.name,
                "size": archive_path.stat().st_size,
                "sha256": sha256_file(archive_path),
            },
            "files": [asdict(entry) for entry in entries],
        }
        manifest_path = temp / "manifest.json"
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC
        fd = os.open(manifest_path, flags, 0o640)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(manifest, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, destination)
        return destination / "manifest.json", entries
    except Exception:
        shutil.rmtree(temp, ignore_errors=True)
        raise
