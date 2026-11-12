"""Battery state/percent tests (SPEC section 12).

Values are taken from SPEC.md, so these tests fail while the round-3 battery
changes are missing instead of silently following the implementation.
"""

from __future__ import annotations

import pytest

from custom_components.bm6.battery import (
    Battery,
    BatteryState,
    BatteryStateAlgorithm,
    BatteryType,
    BatteryVoltage,
    battery_voltage_ranges,
)

from .helpers import entry_data, make_reading

NON_DEVICE_ALGORITHMS = (
    BatteryStateAlgorithm.CVR_DVR,
    BatteryStateAlgorithm.SoC_SoD,
)

AGM_12V_IDLE_VOLTAGE = 13.0


def make_battery(**overrides) -> Battery:
    """Build a Battery from config data the way the coordinator does."""
    return Battery(entry_data(**overrides))


def reading(**kwargs):
    """Return the real-time part the coordinator hands to Battery.update."""
    return make_reading(**kwargs).RealTime


@pytest.mark.parametrize(
    "algorithm", NON_DEVICE_ALGORITHMS, ids=lambda item: item.value
)
def test_none_voltage_is_unknown_without_raising(
    algorithm: BatteryStateAlgorithm,
) -> None:
    """A None voltage gives Unknown for a non-device algorithm."""
    battery = make_battery(state_algorithm=algorithm.value)

    battery.update(reading(), None)

    assert battery.state is BatteryState.Unknown


@pytest.mark.parametrize(
    "algorithm", NON_DEVICE_ALGORITHMS, ids=lambda item: item.value
)
def test_first_idle_update_gives_full_percent(
    algorithm: BatteryStateAlgorithm,
) -> None:
    """An Idle voltage gives percent 100 on the first update of a fresh Battery."""
    battery = make_battery(state_algorithm=algorithm.value)
    assert battery.percent == 0

    battery.update(reading(), AGM_12V_IDLE_VOLTAGE)

    assert battery.state is BatteryState.Idle
    assert battery.percent == 100


@pytest.mark.parametrize(
    ("algorithm", "charging_voltage"),
    [
        (BatteryStateAlgorithm.CVR_DVR, 14.5),
        (BatteryStateAlgorithm.SoC_SoD, 12.3),
    ],
    ids=lambda item: getattr(item, "value", item),
)
def test_state_is_updated_before_percent(
    algorithm: BatteryStateAlgorithm, charging_voltage: float
) -> None:
    """The percent reads the state, so the state is computed first."""
    battery = make_battery(state_algorithm=algorithm.value)

    battery.update(reading(), charging_voltage)
    assert battery.state is BatteryState.Charging

    battery.update(reading(), AGM_12V_IDLE_VOLTAGE)

    assert battery.state is BatteryState.Idle
    assert battery.percent == 100


def test_lifepo4_6v_range_values() -> None:
    """The LiFePO4 6V range exists with the exact SPEC values."""
    voltage_range = battery_voltage_ranges[
        (BatteryType.LiFePO4, BatteryVoltage.V6)
    ]

    assert (voltage_range.dvr.min, voltage_range.dvr.max) == (6.0, 6.75)
    assert (voltage_range.cvr.min, voltage_range.cvr.max) == (7.3, 7.5)
    assert (voltage_range.soc.min, voltage_range.soc.max) == (6.5, 6.75)
    assert (voltage_range.sod.min, voltage_range.sod.max) == (6.0, 6.5)


def test_lifepo4_6v_states() -> None:
    """The LiFePO4 6V range gives sensible charging/discharging states."""
    battery = make_battery(
        battery_type="lifepo4",
        battery_voltage="6V",
        state_algorithm="cvr_dvr",
    )

    battery.update(reading(), 7.4)
    assert battery.state is BatteryState.Charging

    battery.update(reading(), 6.6)
    assert battery.state is BatteryState.Discharging


def test_lifepo4_6v_soc_sod_states() -> None:
    """The same range makes sense for the SoC/SoD algorithm."""
    battery = make_battery(
        battery_type="lifepo4",
        battery_voltage="6V",
        state_algorithm="soc_sod",
    )

    battery.update(reading(), 6.6)
    assert battery.state is BatteryState.Charging

    battery.update(reading(), 7.4)
    assert battery.state is BatteryState.Idle


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        (0, BatteryState.Ok),
        (1, BatteryState.LowVoltage),
        (2, BatteryState.Charging),
        (3, BatteryState.Unknown),
        (99, BatteryState.Unknown),
        (None, BatteryState.Unknown),
    ],
)
def test_device_state_codes_map_to_battery_states(
    code: int | None, expected: BatteryState
) -> None:
    """Device codes are mapped, unknown codes give Unknown without raising."""
    battery = make_battery(state_algorithm="by_device")

    battery.update(reading(state=code), 13.2)

    assert battery.state is expected
