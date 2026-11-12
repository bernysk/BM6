"""BLE connection tests for ``bm6_connect`` (SPEC section 8).

The scanner lookup and ``establish_connection`` are patched at the
``custom_components.bm6.bm6_connect`` namespace, so these tests exercise the
real connector and the real AES frame decoding.  Frame and timing values come
from SPEC.md, not from the implementation.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import time
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from Crypto.Cipher import AES

from custom_components.bm6.bm6_connect import BM6Connector, BM6DeviceError
from custom_components.bm6.const import (
    CHARACTERISTIC_UUID_NOTIFY,
    CHARACTERISTIC_UUID_WRITE,
    CRYPT_KEY,
    MIN_PLAUSIBLE_VOLTAGE,
)

ADDRESS = "AA:BB:CC:DD:EE:FF"
REALTIME_PREFIX = "d15507"

LOOKUP = "custom_components.bm6.bm6_connect.async_scanner_devices_by_address"
ESTABLISH = "custom_components.bm6.bm6_connect.establish_connection"
READ_TIMEOUT = "custom_components.bm6.bm6_connect.BM6_READ_TIMEOUT"
NOTIFY_TIMEOUT = "custom_components.bm6.bm6_connect.REALTIME_NOTIFY_TIMEOUT"


def encrypt(frame: bytes) -> bytes:
    """AES-CBC encrypt a plain-text frame with a zero IV, as the BM6 does."""
    cipher = AES.new(bytes(CRYPT_KEY), AES.MODE_CBC, bytes(16))
    return cipher.encrypt(frame)


def realtime_frame(
    *,
    voltage: float = 13.2,
    temperature: int = 21,
    percent: int = 88,
    state: int = 0,
    rapid_acceleration: int = 0,
    rapid_deceleration: int = 0,
) -> bytes:
    """Build a 16-byte real-time frame with the voltage at hex[14:18]."""
    hex_frame = (
        REALTIME_PREFIX
        + f"{0 if temperature >= 0 else 1:02x}"
        + f"{abs(temperature):02x}"
        + f"{state:02x}"
        + f"{percent:02x}"
        + f"{int(round(voltage * 100)):04x}"
        + f"{rapid_acceleration:04x}"
        + f"{rapid_deceleration:04x}"
        + "000000"
    )
    assert len(hex_frame) == 32
    return bytes.fromhex(hex_frame)


class FakeBleDevice:
    """A hashable stand-in for a BLEDevice (used as a mapping key)."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.address = ADDRESS


def make_scanner(name: str, rssi: int) -> SimpleNamespace:
    """Build a fake BluetoothScannerDevice (scanner, advertisement, ble_device)."""
    return SimpleNamespace(
        scanner=SimpleNamespace(name=name),
        advertisement=SimpleNamespace(rssi=rssi),
        ble_device=FakeBleDevice(name),
    )


def ble_device_from_call(call) -> object:
    """Return the ble_device passed to establish_connection."""
    if len(call.args) >= 2:
        return call.args[1]
    return call.kwargs["ble_device"]


class FakeClient:
    """Record GATT calls and deliver encrypted notifications on write."""

    def __init__(
        self,
        *frames: bytes,
        fail_start_notify: bool = False,
        fail_write: bool = False,
        fail_stop_notify: bool = False,
        fail_disconnect: bool = False,
    ) -> None:
        self.calls: list[tuple] = []
        self.frames = list(frames)
        self.notify_callback = None
        self.subscribed = False
        self.is_connected = True
        self._fail_start_notify = fail_start_notify
        self._fail_write = fail_write
        self._fail_stop_notify = fail_stop_notify
        self._fail_disconnect = fail_disconnect

    async def start_notify(self, uuid, callback) -> None:
        self.calls.append(("start_notify", uuid))
        if self._fail_start_notify:
            raise RuntimeError("start_notify failed")
        self.subscribed = True
        self.notify_callback = callback

    async def write_gatt_char(self, uuid, data, response: bool = False) -> None:
        self.calls.append(("write_gatt_char", uuid))
        if self._fail_write:
            raise RuntimeError("write failed")
        if not self.subscribed:
            return
        for frame in self.frames:
            result = self.notify_callback(uuid, encrypt(frame))
            if inspect.isawaitable(result):
                await result

    async def stop_notify(self, uuid) -> None:
        self.calls.append(("stop_notify", uuid))
        if self._fail_stop_notify:
            raise RuntimeError("stop_notify failed")

    async def disconnect(self) -> None:
        self.calls.append(("disconnect",))
        self.is_connected = False
        if self._fail_disconnect:
            raise RuntimeError("disconnect failed")


def establish(mapping):
    """Return an establish_connection side effect keyed by ble_device."""

    async def _establish(client_cls, ble_device, address, **kwargs):
        outcome = mapping[ble_device]
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    return _establish


def call_names(client: FakeClient) -> list[str]:
    """Return the ordered GATT call names."""
    return [call[0] for call in client.calls]


async def read(scanners, establish_mock):
    """Construct the connector with the patched lookup and read the device."""
    with patch(LOOKUP, return_value=list(scanners)):
        connector = BM6Connector(hass=None, address=ADDRESS)
        return await connector.get_data()


async def test_falls_back_to_the_weaker_scanner() -> None:
    """A failed strong scanner falls back to the next one, in RSSI order."""
    strong = make_scanner("strong", -40)
    weak = make_scanner("weak", -80)
    client = FakeClient(realtime_frame(voltage=13.2))

    with patch(ESTABLISH) as establish_mock:
        establish_mock.side_effect = establish(
            {strong.ble_device: RuntimeError("cannot connect"), weak.ble_device: client}
        )
        data = await read([weak, strong], establish_mock)

    assert data.RealTime.Voltage == pytest.approx(13.2)
    assert data.Advertisement.Scanner == "weak"
    assert [ble_device_from_call(c) for c in establish_mock.call_args_list] == [
        strong.ble_device,
        weak.ble_device,
    ]


async def test_all_scanners_fail_names_each_error() -> None:
    """Every scanner is tried once and the error names each scanner's error."""
    strong = make_scanner("strong", -40)
    weak = make_scanner("weak", -80)

    with patch(ESTABLISH) as establish_mock:
        establish_mock.side_effect = establish(
            {
                strong.ble_device: RuntimeError("strong is broken"),
                weak.ble_device: RuntimeError("weak is broken"),
            }
        )
        with pytest.raises(BM6DeviceError) as excinfo:
            await read([strong, weak], establish_mock)

    assert establish_mock.call_count == len([strong, weak])
    message = str(excinfo.value)
    assert "strong is broken" in message
    assert "weak is broken" in message


async def test_subscribe_happens_before_write() -> None:
    """start_notify is called before the first write_gatt_char."""
    scanner = make_scanner("hci0", -50)
    client = FakeClient(realtime_frame())

    with patch(ESTABLISH) as establish_mock:
        establish_mock.side_effect = establish({scanner.ble_device: client})
        data = await read([scanner], establish_mock)

    names = call_names(client)
    assert "start_notify" in names
    assert "write_gatt_char" in names
    assert names.index("start_notify") < names.index("write_gatt_char")
    assert client.calls[names.index("start_notify")][1] == CHARACTERISTIC_UUID_NOTIFY
    assert client.calls[names.index("write_gatt_char")][1] == CHARACTERISTIC_UUID_WRITE
    assert data.RealTime.Voltage == pytest.approx(13.2)


async def test_overall_timeout_is_a_device_error() -> None:
    """A hanging connection is bounded by BM6_READ_TIMEOUT and raises BM6DeviceError."""
    scanner = make_scanner("hci0", -50)
    started = time.monotonic()

    async def hang(*args, **kwargs):
        await asyncio.sleep(30)

    with patch(NOTIFY_TIMEOUT, 0.5), patch(READ_TIMEOUT, 0.2):
        with patch(ESTABLISH, side_effect=hang):
            with pytest.raises(BM6DeviceError) as excinfo:
                await read([scanner], None)

    elapsed = time.monotonic() - started
    assert not isinstance(excinfo.value, TimeoutError)
    assert elapsed < 2.0, f"the overall read timeout did not fire (took {elapsed:.2f}s)"


async def test_cleanup_after_success() -> None:
    """stop_notify and disconnect are attempted after a successful read."""
    scanner = make_scanner("hci0", -50)
    client = FakeClient(realtime_frame())

    with patch(ESTABLISH) as establish_mock:
        establish_mock.side_effect = establish({scanner.ble_device: client})
        await read([scanner], establish_mock)

    names = call_names(client)
    assert "stop_notify" in names
    assert "disconnect" in names


async def test_cleanup_after_failure_during_read() -> None:
    """stop_notify and disconnect are attempted after the read itself fails."""
    scanner = make_scanner("hci0", -50)
    client = FakeClient(fail_write=True)

    with patch(ESTABLISH) as establish_mock:
        establish_mock.side_effect = establish({scanner.ble_device: client})
        with pytest.raises(BM6DeviceError):
            await read([scanner], establish_mock)

    names = call_names(client)
    assert "stop_notify" in names
    assert "disconnect" in names


@pytest.mark.parametrize(
    ("fail_stop_notify", "fail_disconnect"),
    [(True, False), (False, True), (True, True)],
    ids=["stop-notify", "disconnect", "both"],
)
async def test_cleanup_errors_do_not_escape(
    fail_stop_notify: bool, fail_disconnect: bool
) -> None:
    """Errors while cleaning up are swallowed and the data is still returned."""
    scanner = make_scanner("hci0", -50)
    client = FakeClient(
        realtime_frame(voltage=12.9),
        fail_stop_notify=fail_stop_notify,
        fail_disconnect=fail_disconnect,
    )

    with patch(ESTABLISH) as establish_mock:
        establish_mock.side_effect = establish({scanner.ble_device: client})
        data = await read([scanner], establish_mock)

    assert data.RealTime.Voltage == pytest.approx(12.9)
    assert "disconnect" in call_names(client)


async def test_implausible_frame_falls_back_to_next_scanner() -> None:
    """An implausible (0 V) frame fails that scanner; the next one is used."""
    empty = make_scanner("empty", -40)
    real = make_scanner("real", -70)
    empty_client = FakeClient(realtime_frame(voltage=0.0))
    real_client = FakeClient(realtime_frame(voltage=13.2))

    with patch(NOTIFY_TIMEOUT, 0.1):
        with patch(ESTABLISH) as establish_mock:
            establish_mock.side_effect = establish(
                {empty.ble_device: empty_client, real.ble_device: real_client}
            )
            data = await read([empty, real], establish_mock)

    assert data.RealTime.Voltage == pytest.approx(13.2)
    assert data.RealTime.Voltage >= MIN_PLAUSIBLE_VOLTAGE
    assert data.Advertisement.Scanner == "real"
    assert establish_mock.call_count == 2


async def test_failures_only_log_at_debug(caplog) -> None:
    """bm6_connect logs nothing above debug when every scanner fails."""
    strong = make_scanner("strong", -40)
    weak = make_scanner("weak", -80)

    with caplog.at_level(logging.DEBUG, logger="custom_components.bm6.bm6_connect"):
        caplog.clear()
        with patch(ESTABLISH) as establish_mock:
            establish_mock.side_effect = establish(
                {
                    strong.ble_device: RuntimeError("strong is broken"),
                    weak.ble_device: RuntimeError("weak is broken"),
                }
            )
            with pytest.raises(BM6DeviceError):
                await read([strong, weak], establish_mock)

    noisy = [
        record
        for record in caplog.records
        if record.name.startswith("custom_components.bm6.bm6_connect")
        and record.levelno > logging.DEBUG
    ]
    assert noisy == [], f"bm6_connect logged above debug: {noisy}"
