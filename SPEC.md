# SPEC: translations + keep the last known state

Shared contract for three parallel Tasks. Workers build against this file; if the
code and this spec disagree, the spec wins until the coordinator changes it.
Read `AGENTS.md` first (repo rules, fork remotes, test command).

Fork code is read with `git show <remote>/main:<path>` (remotes `townsmcp`,
`Mpercy-Git`, `Zwer2k`). Never merge a fork branch. Credit the fork in the commit
message when you take code or wording from it.

## 1. Hard rules

- **unique_ids never change.** Every existing entity keeps
  `f"{DOMAIN}_{translation_key.lower()}_{device_address}"`, e.g.
  `bm6_voltage_AA:BB:CC:DD:EE:FF`. The existing translation keys are:
  `voltage, temperature, percentage, state, rssi, device_percentage,
  device_state, rapid_acceleration, rapid_deceleration, bluetooth_scanner`.
  Do not rename any `TRANSLATION_KEY_*` value.
- New entities use the same pattern:
  - `bm6_last_update_<address>` (sensor, translation key `last_update`)
  - `bm6_reachable_<address>` (binary_sensor, translation key `reachable`)
- `en.json` is the source of truth. Every other `translations/*.json` has exactly
  the same set of keys (same nesting), no more, no fewer.

## 2. Constants (`const.py`)

```python
PLATFORMS = {Platform.SENSOR, Platform.BINARY_SENSOR}

TRANSLATION_KEY_LAST_UPDATE = "last_update"
TRANSLATION_KEY_REACHABLE = "reachable"

MISSES_BEFORE_UNREACHABLE = 3       # consecutive failed reads before Reachable goes off
MAX_BACKOFF_UPDATE_INTERVAL = 300   # seconds; back-off ceiling while unreachable
MIN_PLAUSIBLE_VOLTAGE = 5.0         # V; a decoded voltage below this is a bad frame
REALTIME_NOTIFY_TIMEOUT = 10        # seconds to wait for a real-time frame
```

## 3. Coordinator (`BM6DataUpdateCoordinator`)

Public attributes/properties the tests and entities use:

| Name | Type | Meaning |
|---|---|---|
| `data` | `dict \| None` | Last good reading (same keys as today). Never cleared by a miss. |
| `last_good_read` | `datetime \| None` | UTC (`dt_util.utcnow()`) time of the last good read. `None` until the first. |
| `consecutive_misses` | `int` | Failed reads since the last good one. `0` after a success. |
| `reachable` | `bool` | `last_good_read is not None and consecutive_misses < MISSES_BEFORE_UNREACHABLE` |
| `configured_update_interval` | `timedelta` | `timedelta(seconds=config_entry.data[CONF_UPDATE_INTERVAL])` |
| `update_interval` | `timedelta` | Current interval (backed off while missing). |

Behaviour of `_async_update_data`:

1. Read with `BM6Connector(hass=..., address=...).get_data()` (as today). A miss is
   any of: `BM6DeviceError`, any other exception from the connector, `TimeoutError`,
   `data is None`, `data.RealTime is None`, or an implausible frame (below).
2. **Implausible frame** (townsmcp, Zwer2k): `RealTime.Voltage < MIN_PLAUSIBLE_VOLTAGE`
   (this covers the all-zero frame). It counts as a miss; its values are never
   published.
3. **On success:** compute the data dict exactly as today, set `last_good_read`,
   reset `consecutive_misses` to 0, restore `update_interval` to
   `configured_update_interval`, return the dict. If the device was unreachable,
   log once at info that it is reachable again.
4. **On a miss:**
   - `consecutive_misses += 1`; log the miss at **debug** (not warning/error).
   - When `consecutive_misses` reaches `MISSES_BEFORE_UNREACHABLE`, log **one**
     warning that the device is unreachable. No further warnings until it has
     been reachable again.
   - Back off (Mpercy-Git): `update_interval = min(configured * 2**consecutive_misses,
     max(configured, MAX_BACKOFF_UPDATE_INTERVAL))` seconds.
   - If `self.data is not None`: **return `self.data`** (the last good data). Do
     not raise `UpdateFailed`. Entities stay available.
   - If there is no data yet: raise `UpdateFailed`.
5. `get_diagnostic_data()` also returns `last_good_read`, `consecutive_misses`,
   `reachable`; `update_interval` stays in seconds (use `total_seconds()`).

Pass `config_entry=config_entry` to `DataUpdateCoordinator.__init__` (current HA API)
instead of assigning `self.config_entry` / `self.hass` by hand.

## 4. `bm6_connect.py` (minimal, Zwer2k)

- In `_notify_callback`, ignore a real-time frame whose voltage is below
  `MIN_PLAUSIBLE_VOLTAGE` (debug log), so the wait continues for the real frame.
- Bound the wait for the real-time frame with `REALTIME_NOTIFY_TIMEOUT`
  (`asyncio.timeout`); on expiry the scanner attempt fails like any other error.
- No other changes to this file.

## 5. Entities

### 5.1 Sensors (`sensor.py`)

Base class `BM6SensorEntity(CoordinatorEntity[BM6DataUpdateCoordinator], RestoreSensor)`:

- `_attr_has_entity_name = True`; `unique_id` exactly as today.
- Values only through `native_value` (and `native_unit_of_measurement`). No class
  overrides `state` or `unit_of_measurement`; no `_attr_unit_of_measurement`.
- `extra_state_attributes` returns `{}` (or `None`) when `coordinator.data is None`;
  never raises.
- **Restore:** in `async_added_to_hass`, if `coordinator.data is None`, read
  `await self.async_get_last_sensor_data()` and keep its `native_value` (and
  `native_unit_of_measurement`) as the value to show until the coordinator has
  data. While a restored value is shown, `available` is `True`. Once the
  coordinator has data, its data wins.
- `available`: `True` when `coordinator.data is not None` or a restored value is
  held; otherwise `False`.

Per-sensor `native_value`:

| Class | translation_key | native_value | Notes |
|---|---|---|---|
| `BM6VoltageSensor` | voltage | `data[KEY_VOLTAGE_CORRECTED]` | V, VOLTAGE, MEASUREMENT |
| `BM6TemperatureSensor` | temperature | `data[KEY_TEMPERATURE_CORRECTED]` | `native_unit_of_measurement` = configured `CONF_TEMPERATURE_UNIT` (from the config entry, known without data) |
| `BM6PercentageSensor` | percentage | `data[KEY_PERCENTAGE]` | `%`, BATTERY, MEASUREMENT |
| `BM6StateSensor` | state | `data[KEY_STATE]`, or `None` when it is `"unknown"`/not a `BatteryState` | ENUM, `options` = every `BatteryState` value except `"unknown"` |
| `BM6RssiSensor` | rssi | `data[KEY_RSSI]` | unchanged |
| `BM6DevicePercentageSensor` | device_percentage | `data[KEY_DEVICE_PERCENTAGE]` | unchanged |
| `BM6DeviceStateSensor` | device_state | `0→"ok"`, `1→"low_voltage"`, `2→"charging"`, anything else `None` | ENUM, `options = ["ok", "low_voltage", "charging"]` |
| `BM6RapidAccelerationSensor` | rapid_acceleration | unchanged | |
| `BM6RapidDecelerationSensor` | rapid_deceleration | unchanged | |
| `BM6BluetoothScannerSensor` | bluetooth_scanner | unchanged | |
| `BM6LastUpdateSensor` (new) | last_update | `coordinator.last_good_read` | TIMESTAMP, DIAGNOSTIC, enabled by default |

Icons keep their current behaviour.

### 5.2 Binary sensor (`binary_sensor.py`, new)

`BM6ReachableBinarySensor(CoordinatorEntity[BM6DataUpdateCoordinator], BinarySensorEntity)`:

- translation_key `reachable`, `has_entity_name`, device class CONNECTIVITY,
  entity category DIAGNOSTIC, same `device_info` as the sensors.
- `is_on = coordinator.reachable`.
- `available` is always `True` (an unreachable device shows "off", not
  "unavailable").

## 6. Translations (`translations/*.json`)

`en.json` gets these keys added to the current structure:

```json
"entity": {
  "sensor": {
    "last_update": { "name": "Last update" }
  },
  "binary_sensor": {
    "reachable": { "name": "Reachable" }
  }
}
```

Keep every existing key that code uses (config/options steps, errors, abort,
`entity.sensor.*`, `selector.*`, `device_automation.trigger_type.*`), including
the `state_attributes` and `entity.sensor.state.state.*` /
`entity.sensor.device_state.state.*` values. Do **not** add townsmcp keys for
features this repo doesn't have (`preferred_scanners`, `data_description`,
`not_supported`).

Required English names (HA wording):

| Key | Name |
|---|---|
| entity.sensor.voltage | Voltage |
| entity.sensor.temperature | Temperature |
| entity.sensor.percentage | Battery level |
| entity.sensor.state | State |
| entity.sensor.rssi | Signal strength |
| entity.sensor.device_percentage | Device battery level |
| entity.sensor.device_state | Device state |
| entity.sensor.bluetooth_scanner | Bluetooth scanner |
| state values | Charging, Discharging, Idle, OK, Low voltage, Under voltage, Over voltage, Unknown |

## 7. Tests (`tests/`)

- `tests/conftest.py` enables custom integrations
  (`enable_custom_integrations` autouse fixture). `tests/__init__.py` exists.
- `pytest.ini` at the repo root (owned by the tests Task): `testpaths = tests`,
  `asyncio_mode = auto`.
- Translation test: every `translations/*.json` has exactly the key paths of
  `en.json`; every entity class's `translation_key` exists under
  `entity.<platform>.<key>.name`; every ENUM sensor option exists under
  `entity.sensor.<key>.state`; `BatteryState` values (except unknown) exist under
  `entity.sensor.state.state` and `selector.battery_state.options`;
  `BatteryStateAlgorithm`/`BatteryType` values and lower-cased `BatteryVoltage`
  values exist under the matching `selector.*.options`; the `ERROR_*` constants
  exist under `config.error`; `device_trigger.TRIGGER_TYPES` keys exist under
  `device_automation.trigger_type`.
- Coordinator tests mock `custom_components.bm6.coordinator.BM6Connector`.
- `unique_id` stability: the 10 existing unique_ids above, plus the two new ones,
  for a fixed address.

Command that must pass: `.venv/bin/pytest` from the worktree root.

---

# Round 2: BLE connection, manifest, startup log

Everything above still holds (unique_ids, last-known-state behaviour, tests).

## 8. `bm6_connect.py` contract

Module-level names the tests patch (import them by name into `bm6_connect`):

- `async_scanner_devices_by_address` (from `homeassistant.components.bluetooth`, as today)
- `establish_connection` and `BleakClientWithServiceCache` (from `bleak_retry_connector`)

New/changed constants in `const.py` (also imported by name into `bm6_connect`):

```python
VERSION = "1.1.0"
BM6_READ_TIMEOUT = 120         # s, one overall limit for a whole get_data() across all scanners
CONNECT_MAX_ATTEMPTS = 3       # passed to establish_connection(max_attempts=...)
REALTIME_NOTIFY_TIMEOUT = 10   # s, wait for a plausible real-time frame per scanner (exists)
BM6_CLEANUP_TIMEOUT = 5        # s, each of stop_notify / disconnect
```

`BM6Connector(hass=..., address=...)` keeps its constructor: it gets the scanners
with `async_scanner_devices_by_address(hass, address, connectable=True)`, raises
`BM6DeviceError` if there are none, and sorts them by `advertisement.rssi`,
strongest first.

`await connector.get_data() -> BM6Data`:

1. The whole call runs under `asyncio.timeout(BM6_READ_TIMEOUT)`. On expiry it
   raises `BM6DeviceError` (not `TimeoutError`), and the open client is cleaned up.
2. For each scanner in RSSI order:
   1. `client = await establish_connection(BleakClientWithServiceCache, scanner.ble_device, address, max_attempts=CONNECT_MAX_ATTEMPTS)`
   2. `await client.start_notify(CHARACTERISTIC_UUID_NOTIFY, callback)` **before**
      the first `await client.write_gatt_char(CHARACTERISTIC_UUID_WRITE, <encrypted GATT_DATA_REALTIME>, response=True)`.
   3. Wait up to `REALTIME_NOTIFY_TIMEOUT` for a plausible frame (implausible
      frames < `MIN_PLAUSIBLE_VOLTAGE` are ignored, as now). The request may be
      written once more inside that window if no plausible frame came (Zwer2k).
   4. Always clean up: `stop_notify` and `disconnect`, each bounded by
      `BM6_CLEANUP_TIMEOUT`, errors swallowed (debug log).
   5. On a plausible frame return `BM6Data` whose `Advertisement` is from **this**
      scanner. On any failure, log at **debug** and try the next scanner.
3. Only when every scanner failed: raise `BM6DeviceError` naming each scanner's error.
4. `bm6_connect.py` logs nothing above debug (the coordinator owns user-facing logs).

A test client fake needs: `start_notify(uuid, cb)`, `write_gatt_char(uuid, data, response=True)`,
`stop_notify(uuid)`, `disconnect()`, `is_connected`. Frames are AES-CBC encrypted
with `CRYPT_KEY` and a zero IV; a decrypted real-time frame is hex starting with
`d15507`; voltage is `int(hex[14:18], 16) / 100`.

## 9. `manifest.json`

- `"version": "1.1.0"` (same as `const.VERSION`).
- `"requirements"`: `pycryptodome` and `bleak-retry-connector` with lower bounds
  every supported HA (`MIN_REQUIRED_HA_VERSION` up to current) already satisfies;
  no `==` pins that could clash with HA's package constraints.
- `"bluetooth"`: exactly the four `manufacturer_id` matchers and the
  `0000fff0-...` service_uuid matcher (as Mpercy-Git). Every other service_uuid
  matcher goes: `00001800`, `00001801`, `0000180a`, and the generic `0000fee0`.
- `"documentation": "https://github.com/bernysk/BM6"`,
  `"issue_tracker": "https://github.com/bernysk/BM6/issues"`,
  `"codeowners": ["@bernysk"]`.

## 10. Startup log

When the first refresh fails and there is no data yet: **no ERROR record from any
logger** (including HA's DataUpdateCoordinator "Error fetching ..."), and exactly
**one WARNING** that the BM6 at <address> isn't reachable yet. That warning counts
as this outage's unreachable warning: no second warning when the misses reach
`MISSES_BEFORE_UNREACHABLE`. After a later success, the "reachable again" info
log applies as before. Don't call private HA methods (`_async_refresh` etc.).
Entities behave as before (sensors unavailable unless restored, Reachable off).

---

# Round 3: catch up with the forks

Everything above still holds (unique_ids, SPEC 8 call order and logging, etc.).

## 11. Read retries (`bm6_connect.py`, `const.py`; Mpercy-Git)

New constants (imported by name into `bm6_connect`, so tests can patch them there):

```python
CONNECT_SETTLE_TIME = 1.0      # s, wait after connecting, before subscribing
REALTIME_READ_ATTEMPTS = 3     # realtime requests per scanner
REALTIME_ANSWER_TIMEOUT = 3    # s, wait for the answer to one request
```

Per scanner, after `establish_connection`:

1. `await asyncio.sleep(CONNECT_SETTLE_TIME)`, then `start_notify` (still before any write).
2. Up to `REALTIME_READ_ATTEMPTS` times: write the realtime request, then wait up
   to `REALTIME_ANSWER_TIMEOUT` for an answer.
   - A plausible frame (voltage >= `MIN_PLAUSIBLE_VOLTAGE`) ends the read: return it.
   - An **empty answer** (a realtime frame below the floor, including all-zero)
     or **no answer** within `REALTIME_ANSWER_TIMEOUT` -> ask again at once.
3. All attempts sit inside the existing `REALTIME_NOTIFY_TIMEOUT` (per scanner)
   and `BM6_READ_TIMEOUT` (whole read).
4. When every attempt was empty or missing, this scanner fails with a
   `BM6DeviceError` whose message says how many requests were sent and, if any
   answer came, the last answer's hex, e.g.
   `BM6 at <addr> answered without a reading 3 times, last answer was d15507...`
   or `BM6 at <addr> did not answer 3 requests`. Then the next scanner is tried
   (SPEC 8); the final error names each scanner's error.
5. Logging stays at debug.

## 12. Battery (`battery.py`; Mpercy-Git)

- `battery_voltage_ranges[(BatteryType.LiFePO4, BatteryVoltage.V6)]` =
  dvr 6.0-6.75, cvr 7.3-7.5, soc 6.5-6.75, sod 6.0-6.5.
- `_update_state`: when the voltage is `None`, state = `BatteryState.Unknown` and return.
- `Battery.update` (non-device algorithm) updates the state **before** the percent
  (the percent reads the state, e.g. Idle -> 100).
- `_bm6_status_to_battery_state`: a device state code that isn't a
  `BM6RealTimeState` (e.g. 3, 99, None) -> `BatteryState.Unknown`, no exception.

## 13. Config flow and brand (`config_flow.py`, `brand/`; Zwer2k, townsmcp)

- `_is_valid_device(service_info) -> bool` is True exactly when one of the
  manifest's `bluetooth` matchers (read from manifest.json as today, not
  hard-coded) matches:
  - `manufacturer_id` in `service_info.manufacturer_data` and that data starts
    with `bytes(manufacturer_data_start)`; or
  - the matcher's `service_uuid` is in `service_info.service_uuids` (case-insensitive).
  Fix the current `service_data_uuid` lookup, which never matches.
- `_get_devices` offers only matching, not-yet-configured devices. Per-device
  logs at debug; at most one debug summary; nothing at info.
- `async_step_bluetooth` is unchanged (HA only calls it for manifest matches).
- `custom_components/bm6/brand/icon.png` and `icon@2x.png` are byte-identical
  copies of `images/icon.png` and `images/icon@2x.png` (the forks' brand icons
  are these same files).
- manifest.json: no change. HA manifests have no categories field (hassfest
  rejects `categories`); `integration_type: device` stays.
