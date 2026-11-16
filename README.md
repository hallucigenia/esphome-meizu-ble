# ESPHome Meizu BLE

An experimental ESPHome external component for the Meizu R16 BLE infrared puck,
with an optional Home Assistant learning wizard and named-command store.

ESPHome supplies Bluetooth, Wi-Fi, encrypted native API and OTA. This component
adds temperature/humidity/battery polling, acknowledged infrared transmission,
and cancellable ET4007 learning without a vendor APK or native library.

Component version: **0.1.0**. Optional HA adapter: **1.5.0**.
Tested with **ESPHome 2026.10.2 / ESP-IDF 5.5.6 / Home Assistant 2026.11.1**.

## Install

Start with [the complete ESP32 example](examples/esphome-meizu.yaml). Copy
[secrets.example.yaml](examples/secrets.example.yaml) to `examples/secrets.yaml`
and fill in your own Wi-Fi credentials, a fresh 32-byte base64 API encryption
key, and an OTA password. Replace the synthetic puck MAC and select your board.
For installation without cloning this repository, use:

```yaml
external_components:
  - source: github://hallucigenia/esphome-meizu-ble@v0.1.0
    components: [meizu_ble]
```

Each puck needs a dedicated `ble_client` with `auto_connect: false`. Polling uses
short connections, defaults to five minutes, and backs off after failures.
The optional availability and sample-age entities distinguish fresh measurements
from cached values.

Read the [component reference](components/meizu_ble/README.md) for sensors,
local automations, API events, send acknowledgements and learning limits.

## Home Assistant

Add the ESPHome node to HA first. Then install
[`custom_components/meizu_remoter_gateway`](custom_components/meizu_remoter_gateway/README.md)
from this repository or the source-only ZIP on the
[release page](https://github.com/hallucigenia/esphome-meizu-ble/releases/tag/v0.1.0).
The adapter adds the learning wizard and standard `remote.learn_command`,
`remote.send_command` and `remote.delete_command` actions. Sensors and device
firmware updates belong to HA's standard ESPHome integration.

For an existing Meizu HA entry, use **Reconfigure → ESPHome** with the same puck
MAC to retain its remote identity and saved commands. Keep backups and review
sensor entity references before migrating. The HA domain remains
`meizu_remoter_gateway`; do not install a second copy under another domain.

## Verified scope

One R16 and an ESP32-D0WD revision 1.0 with 4 MB Flash passed basic hardware checks:
real sensor samples, a 40-byte learned capture independently converted on MCU and
HA, first-upload and cached-send acknowledgements, an observed HDMI input change,
cancellation, timeout, native OTA and power recovery. Existing remote identity and
nine saved commands were retained.

Long air-conditioner codes, other puck models, long-term reliability and restoring
the original full Flash have not been physically validated. A BLE acknowledgement
confirms the puck's response; it does not confirm the controlled appliance's state.
Migration from another firmware family needs a deliberate USB installation and a
verified full Flash backup; its OTA format and partition layout may differ.

Host regressions and real HA event/service checks are complemented by ESP32,
ESP32-C3 and no-API builds. See [development and test instructions](CONTRIBUTING.md).

## License and acknowledgements

Repository source is released under the [MIT license](LICENSE), retaining
**George Zhao** and **hallucigenia** copyright notices. Thanks to George Zhao's
[original gateway](https://github.com/georgezhao2010/esp32_meizu_remoter_gateway)
and [MIT-licensed HA integration](https://github.com/georgezhao2010/meizu_remoter_gateway)
for the device/protocol groundwork and integration foundation.

This repository contains the ESPHome component, HA adapter and their tests. The
legacy standalone gateway firmware, build images and vendor libraries are not
included. ESPHome and build dependencies retain their own licenses: ESPHome's
C++ runtime is GPLv3 and its Python code is MIT. The repository's MIT license does
not relicense a compiled firmware image or its dependencies. See [NOTICE.md](NOTICE.md).
