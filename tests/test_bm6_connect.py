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
from bleak_retry_connector import BleakClientWithServiceCache

from custom_components.bm6 import bm6_connect, const
from custom_components.bm6.bm6_connect import BM6Connector, BM6DeviceError
from custom_components.bm6.const import (
    CHARACTERISTIC_UUID_NOTIFY,
    CHARACTERISTIC_UUID_WRITE,
    CONNECT_MAX_ATTEMPTS,
    CRYPT_KEY,
    GATT_DATA_REALTIME,
    MIN_PLAUSIBLE_VOLTAGE,
)

ADDRESS = "AA:BB:CC:DD:EE:FF"
REALTIME_PREFIX = "d15507"
HASS = object()

LOOKUP = "custom_components.bm6.bm6_connect.async_scanner_devices_by_address"
ESTABLISH = "custom_components.bm6.bm6_connect.establish_connection"
READ_TIMEOUT = "custom_components.bm6.bm6_connect.BM6_READ_TIMEOUT"
NOTIFY_TIMEOUT = "custom_components.bm6.bm6_connect.REALTIME_NOTIFY_TIMEOUT"
CLEANUP_TIMEOUT = "custom_components.bm6.bm6_connect.BM6_CLEANUP_TIMEOUT"
SETTLE_TIME = "custom_components.bm6.bm6_connect.CONNECT_SETTLE_TIME"
ANSWER_TIMEOUT = "custom_components.bm6.bm6_connect.REALTIME_ANSWER_TIMEOUT"
READ_ATTEMPTS = "custom_components.bm6.bm6_connect.REALTIME_READ_ATTEMPTS"

ZERO_IV = bytes(16)


def aes() -> AES:
    """Return the protocol cipher (CRYPT_KEY, zero IV)."""
    return AES.new(bytes(CRYPT_KEY), AES.MODE_CBC, ZERO_IV)


def encrypt(frame: bytes) -> bytes:
    """AES-CBC encrypt a plain-text frame with a zero IV, as the BM6 does."""
    return aes().encrypt(frame)


def decrypt(payload: bytes) -> bytes:
    """AES-CBC decrypt a payload with the protocol key and a zero IV."""
    return aes().decrypt(payload)


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
        answer_frames: list[bytes | None | tuple[float, bytes]] | None = None,
        log: list[tuple[str, float]] | None = None,
        delayed_frames: tuple[tuple[float, bytes], ...] = (),
        fail_start_notify: bool = False,
        fail_write: bool = False,
        fail_stop_notify: bool = False,
        fail_disconnect: bool = False,
        hang_stop_notify: bool = False,
        hang_disconnect: bool = False,
    ) -> None:
        self.calls: list[tuple] = []
        self.writes: list[tuple[str, bytes, bool]] = []
        self.frames = list(frames)
        self.answer_frames = None if answer_frames is None else list(answer_frames)
        self.log = log
        self.delayed_frames = list(delayed_frames)
        self.notify_callback = None
        self.subscribed = False
        self.is_connected = True
        self._fail_start_notify = fail_start_notify
        self._fail_write = fail_write
        self._fail_stop_notify = fail_stop_notify
        self._fail_disconnect = fail_disconnect
        self._hang_stop_notify = hang_stop_notify
        self._hang_disconnect = hang_disconnect
        self._background: list[asyncio.Task] = []

    async def start_notify(self, uuid, callback) -> None:
        self.calls.append(("start_notify", uuid))
        if self.log is not None:
            self.log.append(("start_notify", time.monotonic()))
        if self._fail_start_notify:
            raise RuntimeError("start_notify failed")
        self.subscribed = True
        self.notify_callback = callback

    async def write_gatt_char(self, uuid, data, response: bool = False) -> None:
        self.calls.append(("write_gatt_char", uuid))
        self.writes.append((uuid, bytes(data), response))
        if self.log is not None:
            self.log.append(("write_gatt_char", time.monotonic()))
        if self._fail_write:
            raise RuntimeError("write failed")
        if not self.subscribed:
            return
        if self.answer_frames is not None:
            index = len(self.writes) - 1
            if index < len(self.answer_frames):
                answer = self.answer_frames[index]
                if isinstance(answer, tuple):
                    delay, frame = answer
                    self._background.append(
                        asyncio.get_running_loop().create_task(
                            self._deliver_later(delay, frame)
                        )
                    )
                elif answer is not None:
                    await self._deliver(answer)
            return
        for frame in self.frames:
            await self._deliver(frame)
        for delay, frame in self.delayed_frames:
            self._background.append(
                asyncio.get_running_loop().create_task(self._deliver_later(delay, frame))
            )

    async def cancel_background(self) -> None:
        for task in self._background:
            task.cancel()
        await asyncio.gather(*self._background, return_exceptions=True)

    async def _deliver_later(self, delay: float, frame: bytes) -> None:
        await asyncio.sleep(delay)
        if self.notify_callback is not None:
            await self._deliver(frame)

    async def _deliver(self, frame: bytes) -> None:
        result = self.notify_callback(CHARACTERISTIC_UUID_NOTIFY, encrypt(frame))
        if inspect.isawaitable(result):
            await result

    async def stop_notify(self, uuid) -> None:
        self.calls.append(("stop_notify", uuid))
        if self._fail_stop_notify:
            raise RuntimeError("stop_notify failed")
        if self._hang_stop_notify:
            await asyncio.Event().wait()

    async def disconnect(self) -> None:
        self.calls.append(("disconnect",))
        self.is_connected = False
        if self._fail_disconnect:
            raise RuntimeError("disconnect failed")
        if self._hang_disconnect:
            await asyncio.Event().wait()


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


def assert_valid_request(client: FakeClient) -> None:
    """The client received the encrypted real-time command and wanted a response."""
    assert client.writes, "no write_gatt_char was issued"
    uuid, payload, response = client.writes[0]
    assert uuid == CHARACTERISTIC_UUID_WRITE
    assert response is True, "write_gatt_char must use response=True"
    assert decrypt(payload) == bytes.fromhex(GATT_DATA_REALTIME)


def assert_connect_call(call, scanner) -> None:
    """establish_connection got the SPEC 8 arguments for this scanner."""
    assert call.args[0] is BleakClientWithServiceCache
    assert call.args[1] is scanner.ble_device
    assert call.args[2] == ADDRESS
    assert call.kwargs["max_attempts"] == CONNECT_MAX_ATTEMPTS


async def read(scanners):
    """Construct the connector with the patched lookup and read the device."""
    with patch(LOOKUP) as lookup_mock:
        lookup_mock.return_value = list(scanners)
        connector = BM6Connector(hass=HASS, address=ADDRESS)
        data = await connector.get_data()
    return data, lookup_mock


def test_spec_default_constants() -> None:
    """SPEC 8 constants have their specified default values."""
    expected = {
        "VERSION": "1.1.0",
        "BM6_READ_TIMEOUT": 120,
        "CONNECT_MAX_ATTEMPTS": 3,
        "CONNECT_SETTLE_TIME": 1.0,
        "REALTIME_READ_ATTEMPTS": 3,
        "REALTIME_ANSWER_TIMEOUT": 3,
        "REALTIME_NOTIFY_TIMEOUT": 10,
        "BM6_CLEANUP_TIMEOUT": 5,
    }
    for name, value in expected.items():
        assert getattr(const, name, None) == value, f"const.{name}"

    # The connector reads these through its module globals, so tests patch them there.
    for name in (
        "BM6_READ_TIMEOUT",
        "CONNECT_MAX_ATTEMPTS",
        "CONNECT_SETTLE_TIME",
        "REALTIME_READ_ATTEMPTS",
        "REALTIME_ANSWER_TIMEOUT",
        "REALTIME_NOTIFY_TIMEOUT",
        "BM6_CLEANUP_TIMEOUT",
    ):
        assert getattr(bm6_connect, name, None) == expected[name], f"bm6_connect.{name}"


def test_no_scanners_raises_device_error() -> None:
    """The constructor raises BM6DeviceError when no scanner sees the device."""
    with patch(LOOKUP) as lookup_mock:
        lookup_mock.return_value = []
        with pytest.raises(BM6DeviceError):
            BM6Connector(hass=HASS, address=ADDRESS)

    lookup_mock.assert_called_once_with(HASS, ADDRESS, connectable=True)


async def test_falls_back_to_the_weaker_scanner() -> None:
    """A failed strong scanner falls back to the next one, in RSSI order."""
    strong = make_scanner("strong", -40)
    weak = make_scanner("weak", -80)
    client = FakeClient(realtime_frame(voltage=13.2))

    with patch(SETTLE_TIME, 0), patch(ESTABLISH) as establish_mock:
        establish_mock.side_effect = establish(
            {strong.ble_device: RuntimeError("cannot connect"), weak.ble_device: client}
        )
        data, lookup_mock = await read([weak, strong])

    lookup_mock.assert_called_once_with(HASS, ADDRESS, connectable=True)
    assert data.RealTime.Voltage == pytest.approx(13.2)
    assert data.Advertisement.Scanner == "weak"
    assert data.Advertisement.RSSI == -80
    calls = establish_mock.call_args_list
    assert [ble_device_from_call(c) for c in calls] == [
        strong.ble_device,
        weak.ble_device,
    ]
    assert_connect_call(calls[0], strong)
    assert_connect_call(calls[1], weak)
    assert_valid_request(client)


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
            await read([strong, weak])

    assert establish_mock.call_count == len([strong, weak])
    message = str(excinfo.value)
    assert "strong is broken" in message
    assert "weak is broken" in message


async def test_subscribe_happens_before_write() -> None:
    """start_notify runs before the first write, with the SPEC 8 request."""
    scanner = make_scanner("hci0", -50)
    client = FakeClient(realtime_frame())

    with patch(SETTLE_TIME, 0), patch(ESTABLISH) as establish_mock:
        establish_mock.side_effect = establish({scanner.ble_device: client})
        data, lookup_mock = await read([scanner])

    lookup_mock.assert_called_once_with(HASS, ADDRESS, connectable=True)
    names = call_names(client)
    assert "start_notify" in names
    assert "write_gatt_char" in names
    assert names.index("start_notify") < names.index("write_gatt_char")
    assert len(client.writes) == 1
    assert client.calls[names.index("start_notify")][1] == CHARACTERISTIC_UUID_NOTIFY
    assert client.calls[names.index("write_gatt_char")][1] == CHARACTERISTIC_UUID_WRITE
    assert_valid_request(client)
    assert data.RealTime.Voltage == pytest.approx(13.2)
    assert data.Advertisement.RSSI == -50


async def test_overall_timeout_is_a_device_error() -> None:
    """A blocked connect is bounded by BM6_READ_TIMEOUT and raises BM6DeviceError."""
    scanner = make_scanner("hci0", -50)
    never = asyncio.Event()

    async def hang(*args, **kwargs):
        await never.wait()

    started = time.monotonic()
    with patch(READ_TIMEOUT, 0.1):
        with patch(ESTABLISH, side_effect=hang):
            try:
                await asyncio.wait_for(read([scanner]), timeout=0.5)
            except BM6DeviceError:
                pass
            except TimeoutError:
                pytest.fail(
                    "BM6_READ_TIMEOUT did not fire; the outer 0.5 s safety bound hit"
                )
            else:
                pytest.fail("a hanging connection did not raise BM6DeviceError")

    elapsed = time.monotonic() - started
    assert elapsed < 0.5, f"the overall read timeout did not fire (took {elapsed:.2f}s)"


async def test_overall_deadline_is_shared_across_scanners() -> None:
    """Later scanner attempts do not get a fresh BM6_READ_TIMEOUT window."""
    first = make_scanner("first", -40)
    second = make_scanner("second", -80)
    never = asyncio.Event()

    async def controlled(client_cls, ble_device, address, **kwargs):
        if ble_device is first.ble_device:
            await asyncio.sleep(0.15)
            raise RuntimeError("first failed")
        await never.wait()

    started = time.monotonic()
    with patch(READ_TIMEOUT, 0.2):
        with patch(ESTABLISH, side_effect=controlled) as establish_mock:
            try:
                await asyncio.wait_for(read([first, second]), timeout=2.0)
            except BM6DeviceError:
                pass
            except TimeoutError:
                pytest.fail("the overall deadline never fired (outer 2 s safety bound)")
            else:
                pytest.fail("the shared overall deadline did not raise BM6DeviceError")

    elapsed = time.monotonic() - started
    assert establish_mock.call_count == 2
    assert elapsed < 0.3, (
        "the second scanner got a fresh deadline: "
        f"took {elapsed:.2f}s with a shared 0.2 s budget"
    )


async def test_overall_deadline_cleans_up_connected_client() -> None:
    """Expiry while a connected client is reading raises and still cleans up."""
    scanner = make_scanner("hci0", -50)
    client = FakeClient()

    started = time.monotonic()
    error = None
    with patch(READ_TIMEOUT, 0.2), patch(NOTIFY_TIMEOUT, 10), patch(SETTLE_TIME, 0):
        with patch(ESTABLISH) as establish_mock:
            establish_mock.side_effect = establish({scanner.ble_device: client})
            try:
                await asyncio.wait_for(read([scanner]), timeout=2.0)
            except BM6DeviceError as err:
                error = err
            except TimeoutError:
                pytest.fail("the overall deadline never fired (outer 2 s safety bound)")
            else:
                pytest.fail("the overall deadline did not raise BM6DeviceError")

    elapsed = time.monotonic() - started
    assert error is not None
    assert "timed out after 0.2s" in str(error), (
        f"the blocked read was not ended by the overall deadline: {error}"
    )
    assert elapsed < 0.8, f"connected read was not bounded (took {elapsed:.2f}s)"
    names = call_names(client)
    assert "stop_notify" in names
    assert "disconnect" in names


async def test_cleanup_after_success() -> None:
    """stop_notify and disconnect are attempted after a successful read."""
    scanner = make_scanner("hci0", -50)
    client = FakeClient(realtime_frame())

    with patch(SETTLE_TIME, 0), patch(ESTABLISH) as establish_mock:
        establish_mock.side_effect = establish({scanner.ble_device: client})
        await read([scanner])

    names = call_names(client)
    assert "stop_notify" in names
    assert "disconnect" in names
    assert_valid_request(client)


async def test_cleanup_after_failure_during_read() -> None:
    """stop_notify and disconnect are attempted after the read itself fails."""
    scanner = make_scanner("hci0", -50)
    client = FakeClient(fail_write=True)

    with patch(SETTLE_TIME, 0), patch(ESTABLISH) as establish_mock:
        establish_mock.side_effect = establish({scanner.ble_device: client})
        with pytest.raises(BM6DeviceError):
            await read([scanner])

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

    with patch(SETTLE_TIME, 0), patch(ESTABLISH) as establish_mock:
        establish_mock.side_effect = establish({scanner.ble_device: client})
        data, _ = await read([scanner])

    assert data.RealTime.Voltage == pytest.approx(12.9)
    assert "disconnect" in call_names(client)


@pytest.mark.parametrize("mode", ["success", "read-failure", "start-notify-failure"])
@pytest.mark.parametrize("hanging", ["stop_notify", "disconnect", "both"])
async def test_cleanup_is_bounded(mode: str, hanging: str) -> None:
    """Hanging cleanup calls are bounded and disconnect runs after stop_notify."""
    scanner = make_scanner("hci0", -50)
    client = FakeClient(
        realtime_frame(voltage=13.2),
        fail_write=mode == "read-failure",
        fail_start_notify=mode == "start-notify-failure",
        hang_stop_notify=hanging in ("stop_notify", "both"),
        hang_disconnect=hanging in ("disconnect", "both"),
    )

    async def bounded_read():
        try:
            return await asyncio.wait_for(read([scanner]), timeout=1.5)
        except TimeoutError:
            pytest.fail(
                "cleanup was not bounded by BM6_CLEANUP_TIMEOUT "
                "(outer 1.5 s safety bound hit)"
            )

    started = time.monotonic()
    with patch(CLEANUP_TIMEOUT, 0.1), patch(SETTLE_TIME, 0):
        with patch(ESTABLISH) as establish_mock:
            establish_mock.side_effect = establish({scanner.ble_device: client})
            if mode == "success":
                data, _ = await bounded_read()
                assert data.RealTime.Voltage == pytest.approx(13.2)
            else:
                with pytest.raises(BM6DeviceError):
                    await bounded_read()

    elapsed = time.monotonic() - started
    assert elapsed < 1.0, f"cleanup was not bounded (took {elapsed:.2f}s)"
    names = call_names(client)
    assert "stop_notify" in names
    assert "disconnect" in names
    assert names.index("stop_notify") < names.index("disconnect")


async def test_implausible_frame_falls_back_to_next_scanner() -> None:
    """An implausible (0 V) frame fails that scanner; the next one is used."""
    empty = make_scanner("empty", -40)
    real = make_scanner("real", -70)
    empty_client = FakeClient(realtime_frame(voltage=0.0))
    real_client = FakeClient(realtime_frame(voltage=13.2))

    with patch(NOTIFY_TIMEOUT, 0.5), patch(ANSWER_TIMEOUT, 0.1), patch(SETTLE_TIME, 0):
        with patch(ESTABLISH) as establish_mock:
            establish_mock.side_effect = establish(
                {empty.ble_device: empty_client, real.ble_device: real_client}
            )
            data, _ = await read([empty, real])

    assert data.RealTime.Voltage == pytest.approx(13.2)
    assert data.RealTime.Voltage >= MIN_PLAUSIBLE_VOLTAGE
    assert data.Advertisement.Scanner == "real"
    assert data.Advertisement.RSSI == -70
    assert establish_mock.call_count == 2


async def test_empty_answer_then_real_answer_succeeds_on_same_scanner() -> None:
    """An empty answer triggers one more request on the same connection."""
    scanner = make_scanner("hci0", -50)
    client = FakeClient(
        answer_frames=[
            realtime_frame(voltage=0.0),
            realtime_frame(voltage=13.2),
        ]
    )

    with patch(NOTIFY_TIMEOUT, 1.0), patch(ANSWER_TIMEOUT, 0.2), patch(SETTLE_TIME, 0):
        with patch(ESTABLISH) as establish_mock:
            establish_mock.side_effect = establish({scanner.ble_device: client})
            data, _ = await read([scanner])

    assert data.RealTime.Voltage == pytest.approx(13.2)
    assert data.Advertisement.Scanner == "hci0"
    assert establish_mock.call_count == 1
    assert call_names(client).count("start_notify") == 1
    assert len(client.writes) == 2


@pytest.mark.parametrize("attempts", [1, 2, 4])
async def test_only_empty_answers_report_the_last_answer(attempts: int) -> None:
    """Every attempt empty fails with the exact request count and last hex."""
    scanner = make_scanner("hci0", -50)
    answers = [realtime_frame(voltage=float(index)) for index in range(attempts)]
    client = FakeClient(answer_frames=answers)

    with patch(NOTIFY_TIMEOUT, 2.0), patch(ANSWER_TIMEOUT, 0.05), patch(
        SETTLE_TIME, 0
    ), patch(READ_ATTEMPTS, attempts):
        with patch(ESTABLISH) as establish_mock:
            establish_mock.side_effect = establish({scanner.ble_device: client})
            with pytest.raises(BM6DeviceError) as excinfo:
                await read([scanner])

    assert establish_mock.call_count == 1
    assert len(client.writes) == attempts
    expected = (
        f"answered without a reading {attempts} times, "
        f"last answer was {answers[-1].hex()}"
    )
    assert expected in str(excinfo.value).lower()


@pytest.mark.parametrize("attempts", [1, 2, 4])
async def test_no_answer_names_the_request_count(attempts: int) -> None:
    """A silent device fails with the exact number of requests it received."""
    scanner = make_scanner("hci0", -50)
    client = FakeClient()

    with patch(NOTIFY_TIMEOUT, 2.0), patch(ANSWER_TIMEOUT, 0.05), patch(
        SETTLE_TIME, 0
    ), patch(READ_ATTEMPTS, attempts):
        with patch(ESTABLISH) as establish_mock:
            establish_mock.side_effect = establish({scanner.ble_device: client})
            with pytest.raises(BM6DeviceError) as excinfo:
                await read([scanner])

    assert establish_mock.call_count == 1
    assert len(client.writes) == attempts
    assert f"did not answer {attempts} requests" in str(excinfo.value).lower()


async def test_delayed_plausible_answer_is_awaited() -> None:
    """A plausible frame that arrives after the write is still read."""
    scanner = make_scanner("hci0", -50)
    client = FakeClient(
        answer_frames=[(0.05, realtime_frame(voltage=13.2))],
    )
    try:
        with patch(NOTIFY_TIMEOUT, 1.0), patch(ANSWER_TIMEOUT, 0.5), patch(
            SETTLE_TIME, 0
        ), patch(READ_ATTEMPTS, 3):
            with patch(ESTABLISH) as establish_mock:
                establish_mock.side_effect = establish({scanner.ble_device: client})
                data, _ = await read([scanner])
    finally:
        await client.cancel_background()

    assert establish_mock.call_count == 1
    assert data.RealTime.Voltage == pytest.approx(13.2)
    assert len(client.writes) == 1


async def test_missing_first_answer_then_delayed_answer_succeeds() -> None:
    """A silent first request is retried and a delayed second answer is read."""
    scanner = make_scanner("hci0", -50)
    client = FakeClient(
        answer_frames=[None, (0.05, realtime_frame(voltage=13.2))],
    )
    try:
        with patch(NOTIFY_TIMEOUT, 1.0), patch(ANSWER_TIMEOUT, 0.1), patch(
            SETTLE_TIME, 0
        ), patch(READ_ATTEMPTS, 3):
            with patch(ESTABLISH) as establish_mock:
                establish_mock.side_effect = establish({scanner.ble_device: client})
                data, _ = await read([scanner])
    finally:
        await client.cancel_background()

    assert establish_mock.call_count == 1
    assert data.RealTime.Voltage == pytest.approx(13.2)
    assert len(client.writes) == 2


async def test_settle_happens_before_subscribe_and_subscribe_before_writes() -> None:
    """The settle sleep runs before start_notify, which precedes every write."""
    scanner = make_scanner("hci0", -50)
    log: list[tuple[str, float]] = []
    client = FakeClient(
        answer_frames=[
            realtime_frame(voltage=0.0),
            realtime_frame(voltage=13.2),
        ],
        log=log,
    )

    async def establish_timed(client_cls, ble_device, address, **kwargs):
        log.append(("establish_connection", time.monotonic()))
        return client

    with patch(SETTLE_TIME, 0.1), patch(ANSWER_TIMEOUT, 0.5):
        with patch(ESTABLISH, side_effect=establish_timed) as establish_mock:
            data, _ = await read([scanner])

    assert establish_mock.call_count == 1
    assert data.RealTime.Voltage == pytest.approx(13.2)
    established = next(at for name, at in log if name == "establish_connection")
    subscribed = next(at for name, at in log if name == "start_notify")
    writes = [at for name, at in log if name == "write_gatt_char"]
    assert subscribed - established >= 0.05
    assert len(writes) == 2
    assert all(at > subscribed for at in writes)


async def test_attempts_stay_within_the_notify_timeout() -> None:
    """Retries are bounded by REALTIME_NOTIFY_TIMEOUT, not attempts * answer."""
    scanner = make_scanner("hci0", -50)
    client = FakeClient()

    started = time.monotonic()
    with patch(NOTIFY_TIMEOUT, 0.2), patch(ANSWER_TIMEOUT, 5.0), patch(
        READ_TIMEOUT, 2.0
    ), patch(SETTLE_TIME, 0), patch(READ_ATTEMPTS, 3):
        with patch(ESTABLISH) as establish_mock:
            establish_mock.side_effect = establish({scanner.ble_device: client})
            try:
                await asyncio.wait_for(read([scanner]), timeout=1.0)
            except BM6DeviceError:
                pass
            except TimeoutError:
                pytest.fail("REALTIME_NOTIFY_TIMEOUT did not bound the attempts")
            else:
                pytest.fail("a silent device did not raise BM6DeviceError")

    elapsed = time.monotonic() - started
    assert elapsed < 0.8, f"the notify timeout did not bound the attempts ({elapsed:.2f}s)"


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
                await read([strong, weak])

    noisy = [
        record
        for record in caplog.records
        if record.name.startswith("custom_components.bm6.bm6_connect")
        and record.levelno > logging.DEBUG
    ]
    assert noisy == [], f"bm6_connect logged above debug: {noisy}"
