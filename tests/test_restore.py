"""Restore tests (SPEC section 5.1)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from homeassistant.core import HomeAssistant, State
from pytest_homeassistant_custom_component.common import (
    mock_restore_cache_with_extra_data,
)

from custom_components.bm6.bm6_connect import BM6DeviceError

from .helpers import ADDRESS, async_setup_entry, entity_id_for, make_entry

RESTORED_VALUE = 12.34
VOLTAGE_UNIQUE_ID = f"bm6_voltage_{ADDRESS}"
# The entity_id follows the translated name of the voltage sensor.  The spec
# fixes the English name ("Voltage"); before the translation rework it was
# "Tension".  Seed both so the test checks restore behaviour, not naming.
VOLTAGE_ENTITY_IDS = (
    "sensor.bm6_aa_bb_cc_dd_ee_ff_voltage",
    "sensor.bm6_aa_bb_cc_dd_ee_ff_tension",
)


def restored_state(entity_id: str) -> tuple[State, dict]:
    """Return a restore cache entry for a voltage sensor."""
    return (
        State(
            entity_id,
            str(RESTORED_VALUE),
            {
                "device_class": "voltage",
                "unit_of_measurement": "V",
                "state_class": "measurement",
            },
        ),
        {
            "native_value": RESTORED_VALUE,
            "native_unit_of_measurement": "V",
        },
    )


async def test_voltage_sensor_restores_last_value(hass: HomeAssistant) -> None:
    """A failed first refresh shows the restored value instead of unavailable."""
    mock_restore_cache_with_extra_data(
        hass, [restored_state(entity_id) for entity_id in VOLTAGE_ENTITY_IDS]
    )

    with patch("custom_components.bm6.coordinator.BM6Connector") as connector_cls:
        connector_cls.return_value.get_data = AsyncMock(
            side_effect=BM6DeviceError("no device")
        )
        entry = make_entry()
        await async_setup_entry(hass, entry)

    coordinator = entry.runtime_data.coordinator
    assert coordinator.data is None

    entity_id = entity_id_for(hass, "sensor", VOLTAGE_UNIQUE_ID)
    assert entity_id is not None, (
        f"no sensor registered with unique_id {VOLTAGE_UNIQUE_ID}"
    )

    state = hass.states.get(entity_id)
    assert state is not None, f"{entity_id} has no state"
    assert state.state != "unavailable"
    assert float(state.state) == pytest.approx(RESTORED_VALUE)
    assert state.attributes["unit_of_measurement"] == "V"
