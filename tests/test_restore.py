"""Restore tests (SPEC section 5.1)."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest

from homeassistant.core import HomeAssistant, State
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    mock_restore_cache_with_extra_data,
)

from custom_components.bm6.bm6_connect import BM6DeviceError

from .helpers import (
    ADDRESS,
    async_setup_entry,
    entity_id_for,
    entity_object,
    make_entry,
    make_reading,
)

VOLTAGE_UNIQUE_ID = f"bm6_voltage_{ADDRESS}"
LAST_UPDATE_UNIQUE_ID = f"bm6_last_update_{ADDRESS}"

RESTORED_VOLTAGE = 12345
RESTORED_VOLTAGE_UNIT = "mV"
RESTORED_TIMESTAMP = datetime(2024, 1, 2, 3, 4, 5, tzinfo=UTC)
FRESH_VOLTAGE = 13.2


def voltage_restore_entry(entity_id: str) -> tuple[State, dict]:
    """Return a restore cache entry for a voltage sensor with a non-default unit."""
    return (
        State(
            entity_id,
            str(RESTORED_VOLTAGE),
            {
                "device_class": "voltage",
                "unit_of_measurement": RESTORED_VOLTAGE_UNIT,
                "state_class": "measurement",
            },
        ),
        {
            "native_value": RESTORED_VOLTAGE,
            "native_unit_of_measurement": RESTORED_VOLTAGE_UNIT,
        },
    )


def timestamp_restore_entry(entity_id: str) -> tuple[State, dict]:
    """Return a restore cache entry for a timestamp sensor."""
    return (
        State(
            entity_id,
            RESTORED_TIMESTAMP.isoformat(),
            {"device_class": "timestamp"},
        ),
        {
            "native_value": {
                "__type": "<class 'datetime.datetime'>",
                "isoformat": RESTORED_TIMESTAMP.isoformat(),
            },
            "native_unit_of_measurement": None,
        },
    )


@pytest.fixture
def connector():
    """Patch the connector used by the coordinator."""
    with patch("custom_components.bm6.coordinator.BM6Connector") as connector_cls:
        connector_cls.return_value.get_data = AsyncMock()
        yield connector_cls


async def setup_failing(hass: HomeAssistant, connector, restore_entries):
    """Set the entry up with a failing connector and the given restore cache."""
    mock_restore_cache_with_extra_data(hass, restore_entries)
    connector.return_value.get_data.side_effect = BM6DeviceError("no device")
    entry = make_entry()
    await async_setup_entry(hass, entry)
    return entry, entry.runtime_data.coordinator


async def deliver_reading(hass: HomeAssistant, coordinator) -> None:
    """Make the next refresh return a good reading."""
    await coordinator.async_refresh()
    await hass.async_block_till_done()


async def test_voltage_sensor_restores_value_and_unit(
    hass: HomeAssistant, connector
) -> None:
    """A failed first refresh restores the value and unit, then live data wins."""
    entity_id = "sensor.bm6_aa_bb_cc_dd_ee_ff_voltage"
    _, coordinator = await setup_failing(
        hass, connector, [voltage_restore_entry(entity_id)]
    )

    assert coordinator.data is None
    assert entity_id_for(hass, "sensor", VOLTAGE_UNIQUE_ID) == entity_id

    entity = entity_object(hass, "sensor", entity_id)
    assert entity is not None
    assert entity.native_value == RESTORED_VOLTAGE
    assert entity.native_unit_of_measurement == RESTORED_VOLTAGE_UNIT
    assert entity.available is True

    state = hass.states.get(entity_id)
    assert state is not None
    assert state.state not in ("unavailable", "unknown")

    connector.return_value.get_data.side_effect = None
    connector.return_value.get_data.return_value = make_reading(
        voltage=FRESH_VOLTAGE
    )
    await deliver_reading(hass, coordinator)

    assert coordinator.data is not None
    assert entity.native_value == pytest.approx(FRESH_VOLTAGE + 0.1)
    assert entity.native_unit_of_measurement == "V"
    state = hass.states.get(entity_id)
    assert float(state.state) == pytest.approx(FRESH_VOLTAGE + 0.1)


async def test_last_update_sensor_restores_timestamp(
    hass: HomeAssistant, connector
) -> None:
    """A failed first refresh restores the last update timestamp, then live data wins."""
    entity_id = "sensor.bm6_aa_bb_cc_dd_ee_ff_last_update"
    _, coordinator = await setup_failing(
        hass, connector, [timestamp_restore_entry(entity_id)]
    )

    assert coordinator.data is None
    assert entity_id_for(hass, "sensor", LAST_UPDATE_UNIQUE_ID) == entity_id

    entity = entity_object(hass, "sensor", entity_id)
    assert entity is not None
    assert entity.native_value == RESTORED_TIMESTAMP
    assert entity.available is True

    state = hass.states.get(entity_id)
    assert state is not None
    assert dt_util.parse_datetime(state.state) == RESTORED_TIMESTAMP

    connector.return_value.get_data.side_effect = None
    connector.return_value.get_data.return_value = make_reading(
        voltage=FRESH_VOLTAGE
    )
    await deliver_reading(hass, coordinator)

    assert coordinator.last_good_read is not None
    assert entity.native_value == coordinator.last_good_read
    state = hass.states.get(entity_id)
    assert dt_util.parse_datetime(state.state) == coordinator.last_good_read.replace(
        microsecond=0
    )
