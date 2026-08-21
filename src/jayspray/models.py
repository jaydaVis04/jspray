from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


def utc_now() -> datetime:
    return datetime.now(UTC)


class ReleaseState(StrEnum):
    DISCOVERED = "DISCOVERED"
    RESOLVED = "RESOLVED"
    QUEUED = "QUEUED"
    DOWNLOADING = "DOWNLOADING"
    DOWNLOADED = "DOWNLOADED"
    VERIFIED = "VERIFIED"
    DECRYPTED = "DECRYPTED"
    EXTRACTED = "EXTRACTED"
    FAILED = "FAILED"


ALLOWED_TRANSITIONS: dict[ReleaseState, frozenset[ReleaseState]] = {
    ReleaseState.DISCOVERED: frozenset(
        {ReleaseState.RESOLVED, ReleaseState.QUEUED, ReleaseState.FAILED}
    ),
    ReleaseState.RESOLVED: frozenset({ReleaseState.QUEUED, ReleaseState.FAILED}),
    ReleaseState.QUEUED: frozenset({ReleaseState.DOWNLOADING, ReleaseState.FAILED}),
    ReleaseState.DOWNLOADING: frozenset({ReleaseState.DOWNLOADED, ReleaseState.FAILED}),
    ReleaseState.DOWNLOADED: frozenset({ReleaseState.VERIFIED, ReleaseState.FAILED}),
    ReleaseState.VERIFIED: frozenset(
        {ReleaseState.DECRYPTED, ReleaseState.EXTRACTED, ReleaseState.FAILED}
    ),
    ReleaseState.DECRYPTED: frozenset({ReleaseState.EXTRACTED, ReleaseState.FAILED}),
    ReleaseState.EXTRACTED: frozenset(),
    ReleaseState.FAILED: frozenset(
        {ReleaseState.QUEUED, ReleaseState.DOWNLOADING, ReleaseState.FAILED}
    ),
}


class StateTransitionError(ValueError):
    pass


def validate_transition(current: ReleaseState, target: ReleaseState) -> None:
    if current == target:
        return
    if target not in ALLOWED_TRANSITIONS[current]:
        raise StateTransitionError(f"invalid firmware state transition: {current} -> {target}")


@dataclass(slots=True)
class FirmwareObservation:
    source: str
    source_record_key: str
    source_url: str
    detail_url: str | None
    model: str
    sales_csc: str
    device_name: str | None = None
    country: str | None = None
    region: str | None = None
    carrier: str | None = None
    ap_version: str | None = None
    csc_version: str | None = None
    cp_version: str | None = None
    data_version: str | None = None
    full_version: str | None = None
    android_version: str | None = None
    one_ui_version: str | None = None
    security_patch: str | None = None
    bootloader_revision: str | None = None
    changelist: str | None = None
    build_date: str | None = None
    source_upload_date: str | None = None
    source_updated_date: str | None = None
    download_status: str | None = None
    expected_size: int | None = None
    source_md5: str | None = None
    observed_at: datetime = field(default_factory=utc_now)
    extra: dict[str, Any] = field(default_factory=dict)

    def as_json_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["observed_at"] = self.observed_at.isoformat()
        return data


@dataclass(slots=True)
class TargetObservation:
    source: str
    source_record_key: str
    source_url: str
    detail_url: str | None
    model: str
    sales_csc: str
    device_name: str | None = None
    country: str | None = None
    region: str | None = None
    carrier: str | None = None
    android_version: str | None = None
    source_updated_date: str | None = None
    observed_at: datetime = field(default_factory=utc_now)
    extra: dict[str, Any] = field(default_factory=dict)

    def as_json_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["observed_at"] = self.observed_at.isoformat()
        return data


@dataclass(frozen=True, slots=True)
class Identity:
    weak_key: str
    strong_key: str | None


@dataclass(frozen=True, slots=True)
class ProbeResult:
    target_id: str
    release_id: str
    model: str
    sales_csc: str
    version: str
    resolvable: bool
    reason: str
