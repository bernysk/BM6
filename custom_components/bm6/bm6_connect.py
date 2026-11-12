"""This module implements communication with BM6 device."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import logging
from enum import Enum
from typing import Optional
from bleak_retry_connector import BleakClientWithServiceCache, establish_connection
from bleak.backends.characteristic import BleakGATTCharacteristic
from bleak.backends.scanner import AdvertisementData
from Crypto.Cipher import AES

from habluetooth import BaseHaScanner, BluetoothScannerDevice
from homeassistant.core import HomeAssistant
from homeassistant.components.bluetooth import async_scanner_devices_by_address

from .const import (
    CHARACTERISTIC_UUID_NOTIFY,
    CHARACTERISTIC_UUID_WRITE,
    CRYPT_KEY,
    MIN_PLAUSIBLE_VOLTAGE,
    REALTIME_NOTIFY_TIMEOUT,
    BM6_READ_TIMEOUT,
    BM6_CLEANUP_TIMEOUT,
    CONNECT_MAX_ATTEMPTS,
    CONNECT_SETTLE_TIME,
    REALTIME_READ_ATTEMPTS,
    REALTIME_ANSWER_TIMEOUT,
    GATT_DATA_REALTIME,
    GATT_NOTIFY_REALTIME_PREFIX,
    GATT_NOTIFY_VERSION_PREFIX,
)

_LOGGER = logging.getLogger(__name__)


class BM6RealTimeState(Enum):
    """Enumeration for BM6 device status."""

    BatteryOk = 0
    LowVoltage = 1
    Charging = 2


@dataclass
class BM6RealTimeData:
    """Class to store the real-time data read from the BM6 device."""

    Voltage: float = 0.0  # Battery voltage in V
    Temperature: int = 0  # Temperature in °C
    Percent: int = 0  # Percentage of power in %
    RapidAcceleration: int = 0
    RapidDeceleration: int = 0
    State: BM6RealTimeState = BM6RealTimeState.BatteryOk  # Status of the battery

    def __init__(self, data: str):
        """Initialize BM6ReadTimeData from a hex string."""
        self.Voltage = int(data[14:18], 16) / 100
        temperature_sign = int(data[6:8], 16)
        self.Temperature = int(data[8:10], 16)
        if temperature_sign == 1:
            self.Temperature = -self.Temperature
        self.Percent = int(data[12:14], 16)
        self.RapidAcceleration = int(data[18:22], 16)
        self.RapidDeceleration = int(data[22:26], 16)
        self.State = int(data[10:12], 16)


@dataclass
class BM6Firmware:
    """Class to store the firmware version of the BM6 device."""

    Version: str = None

    def __init__(self, data: str):
        """Initialize BM6Firmware with version data."""
        self.Version = data


@dataclass
class BM6Advertisement:
    """Class to store the advertisement data of the BM6 device."""

    RSSI: int = None
    Scanner: str = None

    def __init__(
        self,
        advertisement_data: Optional[AdvertisementData],
        ha_scanner: Optional[BaseHaScanner],
    ):
        """Initialize BM6Advertisement with advertisement data."""
        self.RSSI = advertisement_data.rssi if advertisement_data else None
        self.Scanner = ha_scanner.name if ha_scanner else None


@dataclass
class BM6Data:
    """Class to store all data read from the BM6 device."""

    Firmware: Optional[BM6Firmware] = None
    RealTime: Optional[BM6RealTimeData] = None
    Advertisement: Optional[BM6Advertisement] = None

    def __init__(
        self,
        advertisement_data: Optional[AdvertisementData],
        ha_scanner: Optional[BaseHaScanner],
    ):
        """Initialize BM6Data with advertisement data."""
        self.Advertisement = BM6Advertisement(advertisement_data, ha_scanner)


class BM6DeviceError(RuntimeError): ...


class BM6Connector:
    """Class to manage the connection to the BM6 device."""

    def __init__(
        self,
        hass: HomeAssistant,
        address: str
    ):
        """Initialize the BM6Connector with either a HASS or a BLEDevice."""
        self.hass = hass
        self._address: str = address
        self._scanners: list[BluetoothScannerDevice] = None
        self._data: BM6Data = None
        self._empty_answers = 0
        self._last_empty_answer: str | None = None
        _LOGGER.debug("Get device BM6 at %s from HASS", self._address)
        self._scanners: list[BluetoothScannerDevice] = async_scanner_devices_by_address(
                hass, 
                self._address, 
                connectable=True
        )
        if not self._scanners:
            raise BM6DeviceError(f"Bluetooth device {self._address} not found")
        self._scanners.sort(key=lambda scanner: scanner.advertisement.rssi, reverse=True)
        _LOGGER.debug("Device BM6 at %s is seen by scanners %s",
            self._address,
            [
                {
                    "scanner": scanner.scanner.name,
                    "rssi": scanner.advertisement.rssi,
                }
                for scanner in self._scanners
            ],
        )

    def _decrypt(self, data: bytearray) -> bytearray:
        """Decrypt the received data using AES."""
        cipher = AES.new(CRYPT_KEY, AES.MODE_CBC, 16 * b"\0")
        return cipher.decrypt(data)

    def _encrypt(self, data: bytearray) -> bytearray:
        """Encrypt the data to be sent using AES."""
        cipher = AES.new(CRYPT_KEY, AES.MODE_CBC, 16 * b"\0")
        return cipher.encrypt(data)

    async def _notify_callback(
            self, 
            sender: BleakGATTCharacteristic, 
            data: bytearray
    ):
        """Callback function to handle notifications from the BM6 device."""
        message = self._decrypt(data).hex()
        _LOGGER.debug("Received data from BM6 at %s: %s", self._address, message)
        if message.startswith(GATT_NOTIFY_REALTIME_PREFIX):
            real_time = BM6RealTimeData(message)
            if real_time.Voltage < MIN_PLAUSIBLE_VOLTAGE:
                self._empty_answers += 1
                self._last_empty_answer = message
                _LOGGER.debug(
                    "Ignoring implausible real-time frame from BM6 at %s", self._address
                )
                return
            self._data.RealTime = real_time
            _LOGGER.debug(
                "Decoded real-time data from BM6 at %s: %s",
                self._address,
                self._data.RealTime,
            )
        elif message.startswith(GATT_NOTIFY_VERSION_PREFIX):
            self._data.Firmware = BM6Firmware(message)
            _LOGGER.debug(
                "Decoded firmware version from BM6 at %s: %s",
                self._address,
                self._data.Firmware,
            )

    async def _cleanup(self, client: BleakClientWithServiceCache) -> None:
        """Release the connection even if stopping notifications fails or hangs."""
        cancellation = None
        for step, call in (
            ("stop_notify", lambda: client.stop_notify(CHARACTERISTIC_UUID_NOTIFY)),
            ("disconnect", client.disconnect),
        ):
            try:
                async with asyncio.timeout(BM6_CLEANUP_TIMEOUT):
                    await call()
            except asyncio.CancelledError as err:
                # The overall deadline can expire during stop_notify. Still
                # release the connection before propagating cancellation.
                cancellation = err
            except Exception as err:
                _LOGGER.debug(
                    "Error during %s of BM6 at %s: %s", step, self._address, err
                )

        if cancellation is not None:
            raise cancellation

    async def get_data(self) -> BM6Data:
        """Read through the available scanners within one overall deadline."""
        errors: list[str] = []
        try:
            async with asyncio.timeout(BM6_READ_TIMEOUT):
                for scanner in self._scanners:
                    client = None
                    self._data = BM6Data(scanner.advertisement, scanner.scanner)
                    self._empty_answers = 0
                    self._last_empty_answer = None
                    try:
                        client = await establish_connection(
                            BleakClientWithServiceCache,
                            scanner.ble_device,
                            self._address,
                            max_attempts=CONNECT_MAX_ATTEMPTS,
                        )
                        await asyncio.sleep(CONNECT_SETTLE_TIME)
                        # Subscribe first so an immediate reply cannot be lost.
                        await client.start_notify(
                            CHARACTERISTIC_UUID_NOTIFY, self._notify_callback
                        )
                        async with asyncio.timeout(REALTIME_NOTIFY_TIMEOUT):
                            for attempt in range(1, REALTIME_READ_ATTEMPTS + 1):
                                answered = self._empty_answers
                                await client.write_gatt_char(
                                    CHARACTERISTIC_UUID_WRITE,
                                    self._encrypt(bytearray.fromhex(GATT_DATA_REALTIME)),
                                    response=True,
                                )
                                try:
                                    async with asyncio.timeout(REALTIME_ANSWER_TIMEOUT):
                                        while (
                                            self._data.RealTime is None
                                            and self._empty_answers == answered
                                        ):
                                            if not client.is_connected:
                                                raise BM6DeviceError(
                                                    "Disconnected while waiting for data"
                                                )
                                            await asyncio.sleep(0.1)
                                except TimeoutError:
                                    pass
                                if self._data.RealTime is not None:
                                    break
                                _LOGGER.debug(
                                    "BM6 at %s had no reading after request %s",
                                    self._address, attempt,
                                )
                            if self._data.RealTime is None:
                                if self._last_empty_answer is not None:
                                    raise BM6DeviceError(
                                        f"BM6 at {self._address} answered without a reading "
                                        f"{attempt} times, last answer was "
                                        f"{self._last_empty_answer}"
                                    )
                                raise BM6DeviceError(
                                    f"BM6 at {self._address} did not answer {attempt} requests"
                                )
                        return self._data
                    except Exception as err:
                        errors.append(f"{scanner.scanner.name}: {type(err).__name__}: {err}")
                        _LOGGER.debug(
                            "Error while reading BM6 at %s via scanner %s: %s",
                            self._address, scanner.scanner.name, err,
                        )
                    finally:
                        if client is not None:
                            await self._cleanup(client)
        except TimeoutError as err:
            raise BM6DeviceError(
                f"Read of BM6 at {self._address} timed out after {BM6_READ_TIMEOUT}s"
            ) from err
        raise BM6DeviceError(
            f"Error while reading BM6 at {self._address}: {'; '.join(errors)}"
        )
