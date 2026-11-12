"""Manifest tests (SPEC section 9)."""

from __future__ import annotations

import json
from pathlib import Path

from packaging.requirements import Requirement

from custom_components.bm6 import const

MANIFEST_PATH = (
    Path(__file__).resolve().parent.parent / "custom_components" / "bm6" / "manifest.json"
)

MANUFACTURER_IDS = {76, 10346, 58876, 31781}
FFF0 = "0000fff0-0000-1000-8000-00805f9b34fb"

# Approved lower bounds: the versions constrained by HA 2025.1.1.
APPROVED_REQUIREMENTS = {
    "pycryptodome": "3.6.6",
    "bleak-retry-connector": "3.6.0",
}


def load_manifest() -> dict:
    """Return the BM6 manifest."""
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def test_required_packages_have_approved_lower_bounds() -> None:
    """Requirements parse as exactly the two approved, lower-bounded packages."""
    requirements = load_manifest()["requirements"]

    assert isinstance(requirements, list)
    parsed = [Requirement(requirement) for requirement in requirements]
    assert {requirement.name for requirement in parsed} == set(APPROVED_REQUIREMENTS)
    assert len(parsed) == len(APPROVED_REQUIREMENTS)

    for requirement in parsed:
        approved = APPROVED_REQUIREMENTS[requirement.name]
        assert "==" not in str(requirement.specifier), (
            f"{requirement.name} must not be exactly pinned: {requirement}"
        )
        assert str(requirement.specifier) == f">={approved}", (
            f"{requirement.name} must require the approved lower bound "
            f">={approved}: {requirement}"
        )


def test_bluetooth_matchers_are_exact() -> None:
    """Only the four manufacturer matchers and the fff0 service matcher remain."""
    matchers = load_manifest()["bluetooth"]

    manufacturer_ids = {
        matcher["manufacturer_id"] for matcher in matchers if "manufacturer_id" in matcher
    }
    service_uuids = [
        matcher["service_uuid"].lower() for matcher in matchers if "service_uuid" in matcher
    ]

    assert manufacturer_ids == MANUFACTURER_IDS
    assert service_uuids == [FFF0]
    assert len(matchers) == len(MANUFACTURER_IDS) + 1

    for removed in (
        "00001800-0000-1000-8000-00805f9b34fb",
        "00001801-0000-1000-8000-00805f9b34fb",
        "0000180a-0000-1000-8000-00805f9b34fb",
        "0000fee0-0000-1000-8000-00805f9b34fb",
    ):
        assert removed not in service_uuids


def test_version_matches_const() -> None:
    """The manifest version is 1.1.0 and equals const.VERSION."""
    manifest = load_manifest()

    assert manifest["version"] == "1.1.0"
    assert manifest["version"] == const.VERSION


def test_links_and_owner() -> None:
    """Documentation, issue tracker, codeowners and domain are the fork's."""
    manifest = load_manifest()

    assert manifest["documentation"] == "https://github.com/bernysk/BM6"
    assert manifest["issue_tracker"] == "https://github.com/bernysk/BM6/issues"
    assert manifest["codeowners"] == ["@bernysk"]
    assert manifest["domain"] == "bm6"
