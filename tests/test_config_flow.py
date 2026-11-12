"""Config flow device matching tests (SPEC section 13).

The expected Bluetooth matchers are read from manifest.json so the tests and
the manifest cannot drift apart.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.bm6.config_flow import BM6ConfigFlow
from custom_components.bm6.const import DOMAIN

MANIFEST_PATH = (
    Path(__file__).resolve().parent.parent
    / "custom_components"
    / "bm6"
    / "manifest.json"
)
MANIFEST = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
BLUETOOTH_MATCHERS = MANIFEST["bluetooth"]
MANUFACTURER_MATCHERS = [
    matcher for matcher in BLUETOOTH_MATCHERS if "manufacturer_id" in matcher
]
SERVICE_UUIDS = [
    matcher["service_uuid"] for matcher in BLUETOOTH_MATCHERS if "service_uuid" in matcher
]

DISCOVERED = "custom_components.bm6.config_flow.async_discovered_service_info"
CONFIG_FLOW_LOGGER = "custom_components.bm6.config_flow"

CONFIGURED_ADDRESS = "AA:BB:CC:DD:EE:01"
VALID_ADDRESS = "AA:BB:CC:DD:EE:02"
INVALID_ADDRESS = "AA:BB:CC:DD:EE:03"

REMOVED_SERVICE_UUIDS = (
    "00001800-0000-1000-8000-00805f9b34fb",
    "00001801-0000-1000-8000-00805f9b34fb",
    "0000180a-0000-1000-8000-00805f9b34fb",
    "0000fee0-0000-1000-8000-00805f9b34fb",
)

FIRST_MANUFACTURER = MANUFACTURER_MATCHERS[0]
FIRST_MANUFACTURER_ID = FIRST_MANUFACTURER["manufacturer_id"]
FIRST_SIGNATURE = bytes(FIRST_MANUFACTURER["manufacturer_data_start"])


def make_service_info(
    *,
    address: str = VALID_ADDRESS,
    name: str | None = "BM6",
    service_uuids: tuple[str, ...] = (),
    manufacturer_data: dict[int, bytes] | None = None,
    service_data: dict | None = None,
) -> SimpleNamespace:
    """Build the part of BluetoothServiceInfoBleak the flow reads."""
    return SimpleNamespace(
        address=address,
        name=name,
        service_uuids=list(service_uuids),
        manufacturer_data=dict(manufacturer_data or {}),
        service_data=dict(service_data or {}),
    )


def make_flow() -> BM6ConfigFlow:
    """Build a config flow object for the matching helpers."""
    flow = BM6ConfigFlow()
    flow.handler = DOMAIN
    return flow


def manufacturer_info(matcher: dict, data: bytes) -> SimpleNamespace:
    """Return a service info advertising one manufacturer matcher."""
    return make_service_info(
        manufacturer_data={matcher["manufacturer_id"]: data},
    )


def test_manifest_has_the_expected_matchers() -> None:
    """The manifest still has four manufacturer matchers and one service UUID."""
    assert len(MANUFACTURER_MATCHERS) == 4
    assert len(SERVICE_UUIDS) == 1
    assert SERVICE_UUIDS[0].lower() == "0000fff0-0000-1000-8000-00805f9b34fb"


@pytest.mark.parametrize(
    "matcher", MANUFACTURER_MATCHERS, ids=lambda item: str(item["manufacturer_id"])
)
async def test_manufacturer_signature_matches(matcher: dict) -> None:
    """Data starting with a manifest signature matches, trailing bytes ignored."""
    signature = bytes(matcher["manufacturer_data_start"])

    info = manufacturer_info(matcher, signature + b"\x01\x02\x03")

    assert await make_flow()._is_valid_device(info) is True


@pytest.mark.parametrize(
    "uuid",
    [SERVICE_UUIDS[0].lower(), SERVICE_UUIDS[0].upper()],
    ids=["lower", "upper"],
)
async def test_service_uuid_matches_case_insensitively(uuid: str) -> None:
    """The fff0 service UUID matches in either case."""
    info = make_service_info(service_uuids=(uuid,))

    assert await make_flow()._is_valid_device(info) is True


@pytest.mark.parametrize(
    ("service_uuids", "manufacturer_data"),
    [
        ((), {FIRST_MANUFACTURER_ID: b"\x00" + FIRST_SIGNATURE[1:]}),
        ((), {FIRST_MANUFACTURER_ID: b"\xff" * len(FIRST_SIGNATURE)}),
        ((), {FIRST_MANUFACTURER_ID: FIRST_SIGNATURE[:-1]}),
        *[((uuid,), {}) for uuid in REMOVED_SERVICE_UUIDS],
        ((), {}),
    ],
    ids=[
        "wrong-prefix",
        "known-id-other-data",
        "signature-too-short",
        "service-1800",
        "service-1801",
        "service-180a",
        "service-fee0",
        "empty",
    ],
)
async def test_non_bm6_devices_do_not_match(
    service_uuids: tuple[str, ...], manufacturer_data: dict[int, bytes]
) -> None:
    """Wrong prefixes, other data, removed UUIDs and empty data do not match."""
    info = make_service_info(
        service_uuids=service_uuids,
        manufacturer_data=manufacturer_data,
    )

    assert await make_flow()._is_valid_device(info) is False


async def test_is_valid_device_uses_the_manifest_at_runtime() -> None:
    """A synthetic matcher replaces the manifest, so the real matchers stop matching."""
    flow = make_flow()
    synthetic_manufacturer_id = 4242
    synthetic_manufacturer_start = [9, 8, 7, 6]
    synthetic_service_uuid = "0000ABCD-0000-1000-8000-00805F9B34FB"
    synthetic_config = [
        {
            "connectable": True,
            "manufacturer_id": synthetic_manufacturer_id,
            "manufacturer_data_start": synthetic_manufacturer_start,
        },
        {"connectable": True, "service_uuid": synthetic_service_uuid},
    ]

    async def synthetic_bluetooth_config() -> list[dict]:
        return synthetic_config

    flow.bluetooth_config = synthetic_bluetooth_config

    synthetic_manufacturer = make_service_info(
        manufacturer_data={
            synthetic_manufacturer_id: bytes(synthetic_manufacturer_start) + b"\x01"
        }
    )
    assert await flow._is_valid_device(synthetic_manufacturer) is True

    for advertised in (synthetic_service_uuid, synthetic_service_uuid.lower()):
        uuid_info = make_service_info(service_uuids=(advertised,))
        assert await flow._is_valid_device(uuid_info) is True

    real_manufacturer = manufacturer_info(
        FIRST_MANUFACTURER, FIRST_SIGNATURE + b"\x01"
    )
    assert await flow._is_valid_device(real_manufacturer) is False

    real_uuid = make_service_info(service_uuids=(SERVICE_UUIDS[0],))
    assert await flow._is_valid_device(real_uuid) is False


async def test_get_devices_skips_configured_and_logs_nothing_at_info(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """Only matching, not-yet-configured devices are offered; no info logs."""
    configured = manufacturer_info(FIRST_MANUFACTURER, FIRST_SIGNATURE + b"\x00")
    configured.address = CONFIGURED_ADDRESS
    valid = manufacturer_info(FIRST_MANUFACTURER, FIRST_SIGNATURE + b"\x01")
    valid.address = VALID_ADDRESS
    valid.name = "BM6 valid"
    invalid = make_service_info(address=INVALID_ADDRESS, manufacturer_data={})

    MockConfigEntry(
        domain=DOMAIN,
        title=f"BM6 {CONFIGURED_ADDRESS}",
        unique_id=CONFIGURED_ADDRESS,
    ).add_to_hass(hass)

    flow = make_flow()
    flow.hass = hass

    with caplog.at_level(logging.DEBUG, logger=CONFIG_FLOW_LOGGER):
        caplog.clear()
        with patch(DISCOVERED, return_value=[configured, valid, invalid]) as discovered:
            devices = await flow._get_devices()

    discovered.assert_called_once_with(hass, connectable=True)
    assert devices == {VALID_ADDRESS: f"{valid.name} {VALID_ADDRESS}"}
    noisy = [
        record
        for record in caplog.records
        if record.name.startswith(CONFIG_FLOW_LOGGER)
        and record.levelno >= logging.INFO
    ]
    assert noisy == [], f"config_flow logged at info or above: {noisy}"

    summaries = [
        record
        for record in caplog.records
        if record.name.startswith(CONFIG_FLOW_LOGGER)
        and record.levelno == logging.DEBUG
        and (
            "discovery scan" in record.getMessage().lower()
            or "summary" in record.getMessage().lower()
        )
    ]
    assert len(summaries) <= 1, f"more than one debug summary: {summaries}"
