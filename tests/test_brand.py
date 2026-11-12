"""Brand icon tests (SPEC section 13)."""

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
BRAND = ROOT / "custom_components" / "bm6" / "brand"
IMAGES = ROOT / "images"


@pytest.mark.parametrize("name", ["icon.png", "icon@2x.png"])
def test_brand_icons_match_images(name: str) -> None:
    """The brand icons exist and are byte-identical to images/."""
    brand_icon = BRAND / name
    image_icon = IMAGES / name

    assert brand_icon.is_file(), f"{brand_icon} is missing"
    assert image_icon.is_file(), f"{image_icon} is missing"
    assert brand_icon.read_bytes() == image_icon.read_bytes()
