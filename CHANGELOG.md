# Changelog

<!--next-version-placeholder-->

## 1.1.0
### v1.1.0-beta.1
- Sensors keep the last reading when the BM6 can't be read instead of showing "unavailable". The update interval backs off while the device is unreachable and returns to normal after a good read.
- New sensors: "Last update" (time of the last good read) and "Reachable" (connectivity, off after several missed reads).
- Sensors restore their last value after a Home Assistant restart.
- The empty all-zero frame and readings below 5 V are ignored, and waiting for a frame now has a 10 s time limit.
- English translations corrected; the other languages rebased on English, with Slovak and Czech checked by hand.
- Voltage and temperature report proper native values and units, and the state sensors are enums so their translations apply.
- Existing entity IDs are unchanged.
- Based on the forks of Rafciq/BM6 (MIT): townsmcp/BM6 (translations, voltage floor), Mpercy-Git/BM6 (back-off, keeping entities when setup can't read the device) and Zwer2k/BM6 (ignoring the all-zero frame).

### v1.1.0-beta.2
- More reliable Bluetooth connection: connects through `bleak-retry-connector` with retries, and subscribes to notifications before sending the request so a fast reply is no longer lost.
- Every Bluetooth scanner and proxy is tried, strongest signal first; a read only fails after all of them failed.
- Reads are bounded: one time limit for a whole read (120 s), and each cleanup step (stop notifications, disconnect) is limited to 5 s.
- Quieter startup: when the BM6 can't be read at startup, one warning ("isn't reachable yet") replaces the error from Home Assistant.
- Discovery is limited to BM6 devices: the generic Bluetooth service matchers are removed, and discovery uses the BM6 manufacturer data and the fff0 service only.
- The manifest declares its requirements, and documentation, issue tracker and code owner point to bernysk/BM6.
- Existing entity IDs are unchanged.
- Based on the forks of Rafciq/BM6 (MIT): Zwer2k/BM6 (`establish_connection`, subscribe before write, bounded cleanup), Mpercy-Git/BM6 (`establish_connection` with `BleakClientWithServiceCache`, the manifest matcher set) and townsmcp/BM6 (overall read time limit).

### v1.1.0-beta.3
- Retries when the BM6 answers without a reading: after connecting, the integration waits 1 s and then asks up to 3 times (3 s each) when the answer is empty or missing, within the existing read time limits. If every answer is empty, the error includes the last answer.
- Battery: new range for 6 V LiFePO4 batteries; the state is worked out before the percentage so an idle battery shows 100 % from the first reading; a missing voltage gives state "Unknown" instead of an error; unknown status codes from the device map to "Unknown" instead of failing.
- Adding a device: the device list only offers Bluetooth devices that match the BM6 signatures in the manifest, and the list is logged at debug level only.
- The BM6 brand icon is included for Home Assistant 2026.3 and later.
- Existing entity IDs are unchanged.
- Based on the forks of Rafciq/BM6 (MIT): Mpercy-Git/BM6 (read retries, battery ranges and state handling), Zwer2k/BM6 (device matching in the config flow), and townsmcp/BM6 and Zwer2k/BM6 (brand icons).

## 1.0.3
- Added support for multiple Bluetooth scanners/gateways. Now the BM6 device is supported by more than one Bluetooth scanner/gateway. The scanner with the best signal strength is automatically selected to connect to the BM6. If you have more than one scanner/gateway, this version is just for you.
## 1.0.2
- Improvement of code especially translation.
## 1.0.1
- Improvement of code especially translation.
## 1.0.0
- First release.
