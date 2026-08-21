from __future__ import annotations

import hashlib
import re

from jayspray.models import FirmwareObservation, Identity

MODEL_RE = re.compile(r"^(?:SM-)?[A-Z0-9]{3,16}$")
CSC_RE = re.compile(r"^[A-Z0-9]{3,4}$")
VERSION_RE = re.compile(r"^[A-Z0-9._+/-]{4,160}$")
BOOTLOADER_RE = re.compile(r"(?:U|S|E)([0-9A-Z])(?=[A-Z0-9]{4}$)")


def normalized_model(value: str) -> str:
    cleaned = re.sub(r"\s+", "", value).upper()
    if not cleaned.startswith("SM-") and cleaned.startswith("SM"):
        cleaned = f"SM-{cleaned[2:]}"
    if not MODEL_RE.fullmatch(cleaned):
        raise ValueError(f"invalid Samsung model: {value!r}")
    return cleaned


def normalized_csc(value: str) -> str:
    cleaned = value.strip().upper()
    if not CSC_RE.fullmatch(cleaned):
        raise ValueError(f"invalid Samsung CSC: {value!r}")
    return cleaned


def normalized_version(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = re.sub(r"\s+", "", value).upper()
    if not cleaned:
        return None
    if not VERSION_RE.fullmatch(cleaned):
        raise ValueError(f"invalid firmware version: {value!r}")
    return cleaned


def full_version_components(value: str | None) -> tuple[str, ...]:
    normalized = normalized_version(value)
    return tuple(normalized.split("/")) if normalized else ()


def infer_bootloader_revision(ap_version: str | None) -> str | None:
    normalized = normalized_version(ap_version)
    if not normalized or "/" in normalized:
        return None
    match = BOOTLOADER_RE.search(normalized)
    return match.group(1) if match else None


def normalize_observation(observation: FirmwareObservation) -> FirmwareObservation:
    observation.source = observation.source.strip().lower()
    observation.source_record_key = observation.source_record_key.strip()
    observation.model = normalized_model(observation.model)
    observation.sales_csc = normalized_csc(observation.sales_csc)
    observation.ap_version = normalized_version(observation.ap_version)
    observation.csc_version = normalized_version(observation.csc_version)
    observation.cp_version = normalized_version(observation.cp_version)
    observation.data_version = normalized_version(observation.data_version)
    observation.full_version = normalized_version(observation.full_version)
    if observation.full_version:
        parts = full_version_components(observation.full_version)
        if observation.ap_version is None and parts:
            observation.ap_version = parts[0]
        if observation.csc_version is None and len(parts) > 1:
            observation.csc_version = parts[1]
        if observation.cp_version is None and len(parts) > 2:
            observation.cp_version = parts[2]
        if observation.data_version is None and len(parts) > 3:
            observation.data_version = parts[3]
    if observation.bootloader_revision is None:
        observation.bootloader_revision = infer_bootloader_revision(observation.ap_version)
    return observation


def _key(parts: tuple[str, ...]) -> str:
    return hashlib.sha256("\0".join(parts).encode()).hexdigest()


def identity_for(observation: FirmwareObservation) -> Identity:
    item = normalize_observation(observation)
    if not item.ap_version:
        raise ValueError("AP/PDA version is required for canonical identity")
    canonical = _key((item.model, item.ap_version))
    return Identity(weak_key=canonical, strong_key=canonical)


def components_compatible(
    existing: dict[str, str | None], observation: FirmwareObservation
) -> bool:
    for field in ("csc_version", "cp_version", "data_version"):
        left = existing.get(field)
        right = getattr(observation, field)
        if left is not None and right is not None and left != right:
            return False
    return True
