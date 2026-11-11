"""Coordinator behaviour tests (SPEC sections 2, 3 and 5)."""

from __future__ import annotations

from datetime import timedelta
import logging
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from custom_components.bm6 import const
from custom_components.bm6.bm6_connect import BM6DeviceError
from custom_components.bm6.const import (
    KEY_PERCENTAGE,
    KEY_VOLTAGE_CORRECTED,
    PLATFORMS,
)

from .helpers import (
    ADDRESS,
    CONFIGURED_UPDATE_INTERVAL,
    MAX_BACKOFF_UPDATE_INTERVAL,
    MIN_PLAUSIBLE_VOLTAGE,
    MISSES_BEFORE_UNREACHABLE,
    VOLTAGE_OFFSET,
    async_setup_entry,
    entity_id_for,
    make_entry,
    make_reading,
)

VOLTAGE = 13.2
VOLTAGE_CORRECTED = VOLTAGE + VOLTAGE_OFFSET


@pytest.fixture
def connector():
    """Patch the connector used by the coordinator."""
    with patch("custom_components.bm6.coordinator.BM6Connector") as connector_cls:
        connector_cls.return_value.get_data = AsyncMock()
        yield connector_cls


async def setup_ok(hass: HomeAssistant, connector, reading: Any = None):
    """Set the entry up with one good reading."""
    connector.return_value.get_data.return_value = reading or make_reading(
        voltage=VOLTAGE
    )
    entry = make_entry()
    await async_setup_entry(hass, entry)
    return entry, entry.runtime_data.coordinator


def miss(connector) -> None:
    """Make the next connector read fail."""
    connector.return_value.get_data.side_effect = BM6DeviceError("device is gone")


def succeed(connector, reading: Any) -> None:
    """Make the next connector read succeed."""
    connector.return_value.get_data.side_effect = None
    connector.return_value.get_data.return_value = reading


def entity_state(hass: HomeAssistant, platform: str, translation_key: str):
    """Return the State of the entity registered with the expected unique_id."""
    unique_id = f"bm6_{translation_key}_{ADDRESS}"
    entity_id = entity_id_for(hass, platform, unique_id)
    assert entity_id is not None, (
        f"no {platform} entity registered with unique_id {unique_id}"
    )
    return hass.states.get(entity_id)


async def refresh(hass: HomeAssistant, coordinator) -> None:
    """Refresh the coordinator and let the listeners run."""
    await coordinator.async_refresh()
    await hass.async_block_till_done()


def test_spec_constants_exist() -> None:
    """The constants from SPEC section 2 exist with the specified values."""
    assert getattr(const, "MISSES_BEFORE_UNREACHABLE", None) == 3
    assert getattr(const, "MAX_BACKOFF_UPDATE_INTERVAL", None) == 300
    assert getattr(const, "MIN_PLAUSIBLE_VOLTAGE", None) == 5.0
    assert getattr(const, "REALTIME_NOTIFY_TIMEOUT", None) == 10
    assert getattr(const, "TRANSLATION_KEY_LAST_UPDATE", None) == "last_update"
    assert getattr(const, "TRANSLATION_KEY_REACHABLE", None) == "reachable"


def test_binary_sensor_platform_enabled() -> None:
    """The binary_sensor platform is part of PLATFORMS."""
    from homeassistant.const import Platform

    assert Platform.BINARY_SENSOR in PLATFORMS


async def test_success_publishes_data(hass: HomeAssistant, connector) -> None:
    """A good read publishes the data and the entity states."""
    _, coordinator = await setup_ok(hass, connector)

    assert coordinator.data is not None
    assert coordinator.data[KEY_VOLTAGE_CORRECTED] == pytest.approx(VOLTAGE_CORRECTED)
    assert coordinator.data[KEY_PERCENTAGE] is not None
    assert coordinator.consecutive_misses == 0
    assert coordinator.reachable is True
    assert coordinator.last_good_read is not None
    assert coordinator.last_good_read.tzinfo is not None
    assert coordinator.last_good_read.utcoffset() == timedelta(0)

    voltage = entity_state(hass, "sensor", "voltage")
    assert voltage is not None
    assert voltage.state not in ("unavailable", "unknown")
    assert float(voltage.state) == pytest.approx(VOLTAGE_CORRECTED)

    last_update = entity_state(hass, "sensor", "last_update")
    assert last_update is not None
    assert last_update.state not in ("unavailable", "unknown")
    # TIMESTAMP sensors render with second precision.
    assert dt_util.parse_datetime(last_update.state) == coordinator.last_good_read.replace(
        microsecond=0
    )

    reachable = entity_state(hass, "binary_sensor", "reachable")
    assert reachable is not None
    assert reachable.state == "on"


async def test_misses_keep_last_good_data(hass: HomeAssistant, connector) -> None:
    """Misses keep the old data and entities available until unreachable."""
    _, coordinator = await setup_ok(hass, connector)
    good_data = dict(coordinator.data)
    miss(connector)

    for misses in range(1, MISSES_BEFORE_UNREACHABLE):
        await refresh(hass, coordinator)

        assert coordinator.data == good_data
        assert coordinator.last_update_success is True
        assert coordinator.consecutive_misses == misses
        assert coordinator.reachable is True

        voltage = entity_state(hass, "sensor", "voltage")
        assert voltage.state not in ("unavailable", "unknown")
        assert float(voltage.state) == pytest.approx(VOLTAGE_CORRECTED)

        reachable = entity_state(hass, "binary_sensor", "reachable")
        assert reachable is not None
        assert reachable.state == "on"
        assert reachable.state != "unavailable"

    await refresh(hass, coordinator)

    assert coordinator.consecutive_misses == MISSES_BEFORE_UNREACHABLE
    assert coordinator.reachable is False
    assert coordinator.last_update_success is True
    assert coordinator.data == good_data

    voltage = entity_state(hass, "sensor", "voltage")
    assert voltage.state not in ("unavailable", "unknown")
    assert float(voltage.state) == pytest.approx(VOLTAGE_CORRECTED)

    reachable = entity_state(hass, "binary_sensor", "reachable")
    assert reachable.state == "off"
    assert reachable.state != "unavailable"


async def test_one_warning_when_becoming_unreachable(
    hass: HomeAssistant, connector, caplog
) -> None:
    """Exactly one warning is logged when the device becomes unreachable."""
    _, coordinator = await setup_ok(hass, connector)
    miss(connector)

    def bm6_warnings() -> list[logging.LogRecord]:
        return [
            record
            for record in caplog.records
            if record.levelno >= logging.WARNING
            and record.name.startswith("custom_components.bm6")
        ]

    with caplog.at_level(logging.DEBUG):
        caplog.clear()
        for _ in range(MISSES_BEFORE_UNREACHABLE - 1):
            await refresh(hass, coordinator)
            assert bm6_warnings() == [], (
                f"warning/error logged before becoming unreachable: {bm6_warnings()}"
            )

        await refresh(hass, coordinator)
        assert len(bm6_warnings()) == 1, (
            f"expected exactly one unreachable warning, got {bm6_warnings()}"
        )

        for _ in range(2):
            await refresh(hass, coordinator)
            assert len(bm6_warnings()) == 1, (
                f"unreachable warning repeated: {bm6_warnings()}"
            )


async def test_backoff_and_recovery(hass: HomeAssistant, connector) -> None:
    """The update interval backs off while missing and recovers on success."""
    _, coordinator = await setup_ok(hass, connector)
    miss(connector)

    configured = timedelta(seconds=CONFIGURED_UPDATE_INTERVAL)
    ceiling = timedelta(
        seconds=max(CONFIGURED_UPDATE_INTERVAL, MAX_BACKOFF_UPDATE_INTERVAL)
    )
    assert coordinator.configured_update_interval == configured

    for misses in range(1, 5):
        await refresh(hass, coordinator)

        expected = min(configured * 2**misses, ceiling)
        assert coordinator.consecutive_misses == misses
        assert coordinator.update_interval == expected, (
            f"after {misses} misses expected {expected}, "
            f"got {coordinator.update_interval}"
        )

    succeed(connector, make_reading(voltage=VOLTAGE + 0.2))
    await refresh(hass, coordinator)

    assert coordinator.consecutive_misses == 0
    assert coordinator.update_interval == configured
    assert coordinator.reachable is True


async def test_implausible_frame_is_a_miss(hass: HomeAssistant, connector) -> None:
    """A frame below MIN_PLAUSIBLE_VOLTAGE counts as a miss and is not published."""
    _, coordinator = await setup_ok(hass, connector)
    good_data = dict(coordinator.data)

    for misses, voltage in enumerate((0.0, MIN_PLAUSIBLE_VOLTAGE - 2.0), start=1):
        succeed(connector, make_reading(voltage=voltage, percent=1, state=1))
        await refresh(hass, coordinator)

        assert coordinator.data == good_data
        assert coordinator.data[KEY_VOLTAGE_CORRECTED] == pytest.approx(
            VOLTAGE_CORRECTED
        )
        assert coordinator.consecutive_misses == misses

        sensor = entity_state(hass, "sensor", "voltage")
        assert sensor.state not in ("unavailable", "unknown")
        assert float(sensor.state) == pytest.approx(VOLTAGE_CORRECTED)


async def test_first_refresh_failure_without_data(
    hass: HomeAssistant, connector
) -> None:
    """With no data yet a failed read leaves entities unavailable and unreachable."""
    connector.return_value.get_data.side_effect = BM6DeviceError("no device")
    entry = make_entry()
    await async_setup_entry(hass, entry)
    coordinator = entry.runtime_data.coordinator

    assert coordinator.data is None
    assert coordinator.last_update_success is False
    assert coordinator.reachable is False
    assert coordinator.last_good_read is None

    voltage = entity_state(hass, "sensor", "voltage")
    assert voltage.state == "unavailable"

    reachable = entity_state(hass, "binary_sensor", "reachable")
    assert reachable.state == "off"
    assert reachable.state != "unavailable"


@pytest.mark.parametrize(
    "error",
    [BM6DeviceError("boom"), TimeoutError("timed out"), RuntimeError("unexpected")],
    ids=["device-error", "timeout", "unexpected"],
)
async def test_any_read_error_is_a_miss(
    hass: HomeAssistant, connector, error: Exception
) -> None:
    """Any connector exception counts as a miss, not as an UpdateFailed."""
    _, coordinator = await setup_ok(hass, connector)
    connector.return_value.get_data.side_effect = error

    await refresh(hass, coordinator)

    assert coordinator.consecutive_misses == 1
    assert coordinator.last_update_success is True
    assert coordinator.data is not None
    assert coordinator.reachable is True


async def test_diagnostic_data_includes_new_fields(
    hass: HomeAssistant, connector
) -> None:
    """The diagnostics include the new coordinator state."""
    _, coordinator = await setup_ok(hass, connector)

    diagnostics = coordinator.get_diagnostic_data()

    assert diagnostics["last_good_read"] == coordinator.last_good_read
    assert diagnostics["consecutive_misses"] == 0
    assert diagnostics["reachable"] is True
    assert diagnostics["update_interval"] == CONFIGURED_UPDATE_INTERVAL
