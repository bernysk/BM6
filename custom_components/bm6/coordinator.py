"""
This module contains the BM6DataUpdateCoordinator class, which manages fetching data from the BM6 device.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .utils import convert_temperature
from .battery import Battery
from .bm6_connect import BM6Connector, BM6Data, BM6DeviceError
from .const import (
    CONF_TEMPERATURE_UNIT,
    DOMAIN,
    MISSES_BEFORE_UNREACHABLE,
    MAX_BACKOFF_UPDATE_INTERVAL,
    MIN_PLAUSIBLE_VOLTAGE,
    CONF_DEVICE_ADDRESS,
    CONF_UPDATE_INTERVAL,
    CONF_VOLTAGE_OFFSET,
    CONF_TEMPERATURE_OFFSET,
    KEY_BLUETOOTH_SCANNER,
    KEY_CVR_MAX,
    KEY_CVR_MIN,
    KEY_DVR_MAX,
    KEY_DVR_MIN,
    KEY_DEVICE_PERCENTAGE,
    KEY_RAPID_ACCELERATION,
    KEY_RAPID_DECELERATION,
    KEY_STATE_ALGORITHM,
    KEY_PERCENTAGE,
    KEY_RSSI,
    KEY_SOC_MAX,
    KEY_SOC_MIN,
    KEY_SOD_MAX,
    KEY_SOD_MIN,
    KEY_STATE,
    KEY_DEVICE_STATE,
    KEY_TEMPERATURE_CORRECTED,
    KEY_TEMPERATURE_DEVICE,
    KEY_TEMPERATURE_UNIT,
    KEY_VOLTAGE_CORRECTED,
    KEY_VOLTAGE_DEVICE,
)

if TYPE_CHECKING:
    from . import BM6ConfigEntry

_LOGGER = logging.getLogger(__name__)


class BM6DataUpdateCoordinator(DataUpdateCoordinator):
    """Class to manage fetching data from the BM6 device."""

    def __init__(self, hass: HomeAssistant, config_entry: BM6ConfigEntry) -> None:
        """Initialize the coordinator."""
        self.device_address = config_entry.data[CONF_DEVICE_ADDRESS]
        self._battery = Battery(config_entry.data)
        self.last_good_read: datetime | None = None
        self.consecutive_misses = 0
        self.configured_update_interval = timedelta(
            seconds=config_entry.data[CONF_UPDATE_INTERVAL]
        )
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            config_entry=config_entry,
            update_interval=self.configured_update_interval,
        )
        # No successful read yet: avoid HA logging an initial miss as an error.
        # The public coordinator refresh logs its first recovery at info.
        self.last_update_success = False

    async def _async_update_data(self) -> dict:
        """Fetch data from the BM6 device."""
        try:
            connector: BM6Connector = BM6Connector(
                hass=self.hass, address=self.device_address
            )
            data: BM6Data = await connector.get_data()
            if data is None or data.RealTime is None:
                raise BM6DeviceError("No real-time data")
            if data.RealTime.Voltage < MIN_PLAUSIBLE_VOLTAGE:
                raise BM6DeviceError(f"Implausible voltage: {data.RealTime.Voltage} V")
            voltage_corrected = (
                data.RealTime.Voltage + self.config_entry.data[CONF_VOLTAGE_OFFSET]
            )
            temperature_unit = self.config_entry.data[CONF_TEMPERATURE_UNIT]
            temperature_corrected = (
                convert_temperature(data.RealTime.Temperature, temperature_unit)
                + self.config_entry.data[CONF_TEMPERATURE_OFFSET]
            )
            self._battery.update(data.RealTime, voltage_corrected)
            result = {
                KEY_VOLTAGE_DEVICE: data.RealTime.Voltage,
                KEY_VOLTAGE_CORRECTED: voltage_corrected,
                KEY_TEMPERATURE_DEVICE: data.RealTime.Temperature,
                KEY_TEMPERATURE_UNIT: temperature_unit,
                KEY_TEMPERATURE_CORRECTED: temperature_corrected,
                KEY_PERCENTAGE: self._battery.percent,
                KEY_STATE: self._battery.state.value,
                KEY_RSSI: data.Advertisement.RSSI if data.Advertisement else None,
                KEY_DVR_MIN: self._battery.range.dvr.min,
                KEY_DVR_MAX: self._battery.range.dvr.max,
                KEY_CVR_MIN: self._battery.range.cvr.min,
                KEY_CVR_MAX: self._battery.range.cvr.max,
                KEY_SOD_MIN: self._battery.range.sod.min,
                KEY_SOD_MAX: self._battery.range.sod.max,
                KEY_SOC_MIN: self._battery.range.soc.min,
                KEY_SOC_MAX: self._battery.range.soc.max,
                KEY_STATE_ALGORITHM: self._battery.info.state_algorithm.value,
                KEY_DEVICE_PERCENTAGE: data.RealTime.Percent,
                KEY_DEVICE_STATE: data.RealTime.State,
                KEY_RAPID_ACCELERATION: data.RealTime.RapidAcceleration,
                KEY_RAPID_DECELERATION: data.RealTime.RapidDeceleration,
                KEY_BLUETOOTH_SCANNER: (
                    data.Advertisement.Scanner if data.Advertisement else None
                ),
            }
            was_unreachable = (
                self.consecutive_misses >= MISSES_BEFORE_UNREACHABLE
                or (self.last_good_read is None and self.consecutive_misses > 0)
            )
            self.last_good_read = dt_util.utcnow()
            self.consecutive_misses = 0
            self.update_interval = self.configured_update_interval
            if was_unreachable:
                _LOGGER.info("BM6 at %s is reachable again", self.device_address)
            return result
        except Exception as err:
            self.consecutive_misses += 1
            _LOGGER.debug(
                "BM6 at %s read missed (%s): %s",
                self.device_address, self.consecutive_misses, err,
            )
            if self.last_good_read is None:
                if self.consecutive_misses == 1:
                    _LOGGER.warning(
                        "BM6 at %s isn't reachable yet", self.device_address
                    )
            elif self.consecutive_misses == MISSES_BEFORE_UNREACHABLE:
                _LOGGER.warning("BM6 at %s is unreachable", self.device_address)
            configured = self.configured_update_interval.total_seconds()
            ceiling = max(configured, MAX_BACKOFF_UPDATE_INTERVAL)
            # Bound the exponent too, so prolonged outages cannot overflow.
            interval = min(configured * 2 ** min(self.consecutive_misses, 1023), ceiling)
            self.update_interval = timedelta(seconds=interval)
            if self.data is not None:
                return self.data
            raise UpdateFailed(f"BM6 read failed: {err}") from err

    @property
    def reachable(self) -> bool:
        """Whether a good reading exists without too many subsequent misses."""
        return (
            self.last_good_read is not None
            and self.consecutive_misses < MISSES_BEFORE_UNREACHABLE
        )

    def get_diagnostic_data(self) -> dict:
        """Return diagnostic data for the BM6 device."""
        return {
            "device_address": self.device_address,
            "battery": self._battery.get_diagnostic_data(),
            "data": self.data,
            "last_update_success": self.last_update_success,
            "last_exception": self.last_exception,
            "update_interval": self.update_interval.total_seconds(),
            "microsecond": self._microsecond,
            "last_good_read": self.last_good_read,
            "consecutive_misses": self.consecutive_misses,
            "reachable": self.reachable,
        }
