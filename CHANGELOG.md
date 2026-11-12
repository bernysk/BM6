# Changelog

<!--next-version-placeholder-->

## 1.1.0
First full release of the fork. It combines the three pre-releases v1.1.0-beta.1, v1.1.0-beta.2 and v1.1.0-beta.3.

### Sensors keep their last known value
- Sensors keep the last reading when the BM6 can't be read instead of showing "unavailable".
- The update interval backs off while the device is unreachable and returns to normal after a good read.
- Sensors restore their last value after a Home Assistant restart.
- New sensors: "Last update" (time of the last good read) and "Reachable" (connectivity, off after 3 missed reads).

### More reliable Bluetooth
- Connects through `bleak-retry-connector` with retries, and subscribes to notifications before sending the request, so a fast reply is no longer lost.
- Every Bluetooth scanner and proxy is tried, strongest signal first; a read only fails after all of them failed.
- Read retries: after connecting, the integration waits 1 s and asks up to 3 times (3 s each) when the answer is empty or missing. If every answer is empty, the error includes the last answer.
- Reads are bounded: one time limit for a whole read (120 s), and each cleanup step (stop notifications, disconnect) is limited to 5 s.
- The empty all-zero frame and readings below 5 V are ignored; waiting for a frame has a 10 s time limit.
- When the BM6 can't be read at startup, one warning ("isn't reachable yet") replaces the error from Home Assistant.

### Battery
- New range for 6 V LiFePO4 batteries.
- The state is worked out before the percentage, so an idle battery shows 100 % from the first reading.
- A missing voltage gives state "Unknown" instead of an error.
- Unknown status codes from the device map to "Unknown" instead of failing.
- Voltage and temperature report proper native values and units, and the state sensors are enums so their translations apply.

### Setup and discovery
- Discovery is limited to BM6 devices: the generic Bluetooth service matchers are removed, and discovery uses the BM6 manufacturer data and the `fff0` service only.
- Adding a device: the device list only offers Bluetooth devices that match the BM6 signatures in the manifest; the list is logged at debug level only.
- The manifest declares its requirements (`pycryptodome`, `bleak-retry-connector`), and documentation, issue tracker and code owner point to bernysk/BM6.
- The BM6 brand icon is included for Home Assistant 2026.3 and later.

### Translations
- English translations corrected (the earlier machine translation from Polish is fixed).
- The other languages are rebased on English; Slovak and Czech were checked by hand.

### Compatibility
- Existing entity IDs and unique IDs are unchanged.

### Credits
Based on the forks of Rafciq/BM6 (MIT): townsmcp/BM6 (translations, voltage floor, overall read time limit), Mpercy-Git/BM6 (back-off, keeping entities when setup can't read the device, read retries, battery ranges and state handling), and Zwer2k/BM6 (ignoring the all-zero frame, `establish_connection`, subscribe before write, device matching in the config flow).

## 1.0.3
- Added support for multiple Bluetooth scanners/gateways. Now the BM6 device is supported by more than one Bluetooth scanner/gateway. The scanner with the best signal strength is automatically selected to connect to the BM6. If you have more than one scanner/gateway, this version is just for you.
## 1.0.2
- Improvement of code especially translation.
## 1.0.1
- Improvement of code especially translation.
## 1.0.0
- First release.
