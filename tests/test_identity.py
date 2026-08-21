from __future__ import annotations

import pytest

from fwtool.identity import identity_for, infer_bootloader_revision, normalized_model
from fwtool.models import FirmwareObservation


def observation(csc: str, pda: str, full_version: str) -> FirmwareObservation:
    parts = full_version.split("/")
    return FirmwareObservation(
        source="samsung_fus",
        source_record_key=f"SM-S928U1:{csc}:{full_version}",
        source_url="samsung-fus:SmartHistory",
        detail_url=None,
        model="sm-s928u1",
        sales_csc=csc.lower(),
        ap_version=pda,
        csc_version=parts[1],
        cp_version=parts[2],
        data_version=parts[3],
        full_version=full_version,
    )


def test_same_model_and_pda_are_same_identity_across_csc() -> None:
    xaa = observation(
        "XAA",
        "S928U1UES4AXH1",
        "S928U1UES4AXH1/S928U1OYM4AXH1/S928U1UES4AXH1/S928U1UES4AXH1",
    )
    eux = observation(
        "EUX",
        "S928U1UES4AXH1",
        "S928U1UES4AXH1/S928U1OXM4AXH2/S928U1UES4AXH1/S928U1UES4AXH1",
    )
    assert identity_for(xaa) == identity_for(eux)


def test_different_pda_is_different_identity() -> None:
    first = observation(
        "XAA",
        "S928U1UES4AXH1",
        "S928U1UES4AXH1/S928U1OYM4AXH1/S928U1UES4AXH1/S928U1UES4AXH1",
    )
    second = observation(
        "XAA",
        "S928U1UES5AYB1",
        "S928U1UES5AYB1/S928U1OYM5AYB1/S928U1UES5AYB1/S928U1UES5AYB1",
    )
    assert identity_for(first) != identity_for(second)


def test_model_and_bootloader_normalization() -> None:
    assert normalized_model("sms928u1") == "SM-S928U1"
    assert infer_bootloader_revision("S928U1UES4AXH1") == "4"
    assert infer_bootloader_revision("A256EXXSDEZG2") == "D"
    with pytest.raises(ValueError):
        normalized_model("../../phone")
