"""Sensor entity values and metadata, unit level (SPEC section 5.1)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.const import (
    PERCENTAGE,
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    UnitOfElectricPotential,
    UnitOfTemperature,
)

from custom_components.bm6.battery import BatteryState
from custom_components.bm6.const import (
    KEY_BLUETOOTH_SCANNER,
    KEY_DEVICE_PERCENTAGE,
    KEY_DEVICE_STATE,
    KEY_PERCENTAGE,
    KEY_RAPID_ACCELERATION,
    KEY_RAPID_DECELERATION,
    KEY_RSSI,
    KEY_STATE,
    KEY_TEMPERATURE_CORRECTED,
    KEY_VOLTAGE_CORRECTED,
)
from custom_components.bm6.sensor import (
    BM6BluetoothScannerSensor,
    BM6DevicePercentageSensor,
    BM6DeviceStateSensor,
    BM6LastUpdateSensor,
    BM6PercentageSensor,
    BM6RapidAccelerationSensor,
    BM6RapidDecelerationSensor,
    BM6RssiSensor,
    BM6StateSensor,
    BM6TemperatureSensor,
    BM6VoltageSensor,
)

from .helpers import fake_coordinator

LAST_GOOD_READ = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)

ALL_SENSOR_CLASSES = [
    BM6VoltageSensor,
    BM6TemperatureSensor,
    BM6PercentageSensor,
    BM6StateSensor,
    BM6RssiSensor,
    BM6DevicePercentageSensor,
    BM6DeviceStateSensor,
    BM6RapidAccelerationSensor,
    BM6RapidDecelerationSensor,
    BM6BluetoothScannerSensor,
    BM6LastUpdateSensor,
]

EXPECTED_BATTERY_STATES = [
    state.value for state in BatteryState if state is not BatteryState.Unknown
]


def make_sensor(cls, data=None):
    """Instantiate a sensor against a fake coordinator."""
    return cls(fake_coordinator(data, last_good_read=LAST_GOOD_READ))


def test_state_sensor_valid_values() -> None:
    """Every BatteryState except unknown is returned unchanged."""
    for state in BatteryState:
        if state is BatteryState.Unknown:
            continue
        sensor = make_sensor(BM6StateSensor, {KEY_STATE: state.value})
        assert sensor.native_value == state.value, state


@pytest.mark.parametrize("raw", ["unknown", "not_a_state", "", None])
def test_state_sensor_invalid_values_are_none(raw: str | None) -> None:
    """Unknown and invalid states are reported as None."""
    sensor = make_sensor(BM6StateSensor, {KEY_STATE: raw})
    assert sensor.native_value is None


def test_state_sensor_metadata() -> None:
    """The state sensor is an ENUM with every BatteryState except unknown."""
    sensor = make_sensor(BM6StateSensor, {KEY_STATE: "idle"})
    assert sensor.device_class is SensorDeviceClass.ENUM
    assert set(sensor.options) == set(EXPECTED_BATTERY_STATES)
    assert len(sensor.options) == len(EXPECTED_BATTERY_STATES)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(0, "ok"), (1, "low_voltage"), (2, "charging"), (99, None), (None, None)],
)
def test_device_state_sensor_values(raw, expected) -> None:
    """Device states map to ok/low_voltage/charging and nothing else."""
    sensor = make_sensor(BM6DeviceStateSensor, {KEY_DEVICE_STATE: raw})
    assert sensor.native_value == expected


def test_device_state_sensor_metadata() -> None:
    """The device state sensor is an ENUM with exactly the three options."""
    sensor = make_sensor(BM6DeviceStateSensor, {KEY_DEVICE_STATE: 0})
    assert sensor.device_class is SensorDeviceClass.ENUM
    assert sensor.options == ["ok", "low_voltage", "charging"]


def test_numeric_sensor_values_and_metadata() -> None:
    """Voltage/temperature/percentage/RSSI expose the SPEC values and units."""
    data = {
        KEY_VOLTAGE_CORRECTED: 13.3,
        KEY_TEMPERATURE_CORRECTED: 21.0,
        KEY_PERCENTAGE: 77,
        KEY_RSSI: -61,
        KEY_DEVICE_PERCENTAGE: 55,
        KEY_RAPID_ACCELERATION: 12,
        KEY_RAPID_DECELERATION: 3,
        KEY_BLUETOOTH_SCANNER: "hci0",
    }

    voltage = make_sensor(BM6VoltageSensor, data)
    assert voltage.native_value == 13.3
    assert voltage.native_unit_of_measurement == UnitOfElectricPotential.VOLT
    assert voltage.device_class is SensorDeviceClass.VOLTAGE
    assert voltage.state_class is SensorStateClass.MEASUREMENT

    temperature = make_sensor(BM6TemperatureSensor, data)
    assert temperature.native_value == 21.0
    assert temperature.native_unit_of_measurement == UnitOfTemperature.CELSIUS
    assert temperature.device_class is SensorDeviceClass.TEMPERATURE

    percentage = make_sensor(BM6PercentageSensor, data)
    assert percentage.native_value == 77
    assert percentage.native_unit_of_measurement == PERCENTAGE
    assert percentage.device_class is SensorDeviceClass.BATTERY

    rssi = make_sensor(BM6RssiSensor, data)
    assert rssi.native_value == -61
    assert rssi.native_unit_of_measurement == SIGNAL_STRENGTH_DECIBELS_MILLIWATT
    assert rssi.device_class is SensorDeviceClass.SIGNAL_STRENGTH

    device_percentage = make_sensor(BM6DevicePercentageSensor, data)
    assert device_percentage.native_value == 55
    assert device_percentage.native_unit_of_measurement == PERCENTAGE

    acceleration = make_sensor(BM6RapidAccelerationSensor, data)
    assert acceleration.native_value == 12
    assert acceleration.state_class is SensorStateClass.MEASUREMENT

    deceleration = make_sensor(BM6RapidDecelerationSensor, data)
    assert deceleration.native_value == 3

    scanner = make_sensor(BM6BluetoothScannerSensor, data)
    assert scanner.native_value == "hci0"


def test_last_update_sensor_value() -> None:
    """The last update sensor exposes the coordinator timestamp."""
    sensor = make_sensor(BM6LastUpdateSensor, {KEY_VOLTAGE_CORRECTED: 13.3})
    assert sensor.native_value == LAST_GOOD_READ
    assert sensor.device_class is SensorDeviceClass.TIMESTAMP


@pytest.mark.parametrize("sensor_class", ALL_SENSOR_CLASSES, ids=lambda cls: cls.__name__)
def test_no_data_values_and_attributes(sensor_class) -> None:
    """Without data, values are None and attributes are empty, never raising."""
    sensor = make_sensor(sensor_class, None)
    assert sensor.native_value is None
    assert sensor.extra_state_attributes in ({}, None)
