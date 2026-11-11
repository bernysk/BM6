"""unique_id stability tests (SPEC sections 1 and 7)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from homeassistant.core import HomeAssistant

from .helpers import (
    ADDRESS,
    async_setup_entry,
    config_entry_unique_ids,
    make_entry,
    make_reading,
)

EXPECTED_UNIQUE_IDS = {
    "bm6_voltage_AA:BB:CC:DD:EE:FF",
    "bm6_temperature_AA:BB:CC:DD:EE:FF",
    "bm6_percentage_AA:BB:CC:DD:EE:FF",
    "bm6_state_AA:BB:CC:DD:EE:FF",
    "bm6_rssi_AA:BB:CC:DD:EE:FF",
    "bm6_device_percentage_AA:BB:CC:DD:EE:FF",
    "bm6_device_state_AA:BB:CC:DD:EE:FF",
    "bm6_rapid_acceleration_AA:BB:CC:DD:EE:FF",
    "bm6_rapid_deceleration_AA:BB:CC:DD:EE:FF",
    "bm6_bluetooth_scanner_AA:BB:CC:DD:EE:FF",
    "bm6_last_update_AA:BB:CC:DD:EE:FF",
    "bm6_reachable_AA:BB:CC:DD:EE:FF",
}


async def test_unique_ids_are_stable(hass: HomeAssistant) -> None:
    """The registry contains exactly the expected unique_ids for the entry."""
    with patch("custom_components.bm6.coordinator.BM6Connector") as connector_cls:
        connector_cls.return_value.get_data = AsyncMock(
            return_value=make_reading(voltage=13.2)
        )
        entry = make_entry()
        await async_setup_entry(hass, entry)

    actual = config_entry_unique_ids(hass, entry)

    missing = sorted(EXPECTED_UNIQUE_IDS - actual)
    extra = sorted(actual - EXPECTED_UNIQUE_IDS)

    assert not missing and not extra, (
        f"unique_ids for {ADDRESS} changed: missing={missing} extra={extra}"
    )
