# Meizu R16 BLE external component

Experimental component version **0.1.0**, targeting **ESPHome 2026.10.2+ with ESP-IDF**.
The ESP32 connects directly to a Meizu R16 puck. ESPHome owns the Bluetooth stack,
networking, encrypted native API and OTA. The component supplies battery-powered
polling, acknowledged IR transmission and cancellable ET4007 capture/conversion.

Basic hardware checks passed on one Meizu R16 with an ESP32-D0WD revision 1.0,
4 MB Flash, ESPHome 2026.10.2 / ESP-IDF 5.5.6 and Home Assistant 2026.11.1.
The trial covered real sensor samples, a 40-byte capture converted independently
on the MCU and HA, first-upload `0x02` and cached `0x04` acknowledgements, an
observed HDMI input change, cancellation, timeout, native OTA and power recovery.
It also retained the original HA entry, remote identity and nine saved commands.
This remains experimental; preserve a full Flash backup and the old installation.

## Configuration

From a cloned repository, use [the complete example](../../examples/esphome-meizu.yaml).
For a Git-based installation, replace its `external_components` section with:

```yaml
external_components:
  - source: github://hallucigenia/esphome-meizu-ble@v0.1.0
    components: [meizu_ble]
```

Pin the source to a reviewed commit for reproducible deployments. Each puck needs
its own dedicated `ble_client` and `meizu_ble` instance:

```yaml
ble_client:
  - id: puck_client
    mac_address: "02:00:00:00:00:01"  # Synthetic; replace with your puck MAC.
    auto_connect: false

meizu_ble:
  - id: puck
    ble_client_id: puck_client
    update_interval: 5min
    temperature:
      name: Puck Temperature
    humidity:
      name: Puck Humidity
    battery:
      name: Puck Battery
```

`voltage`, `rssi`, `sample_age` and the `available` binary sensor are optional.
Polling intervals are whole minutes from 1 to 30. Polls connect, read both temperature/
humidity and voltage, and disconnect; they do not keep the puck connected continuously.
Failed polls retry with exponential backoff from five seconds to fifteen minutes.
Stale or failed samples become unknown; a successful IR command is not a new sample.
Battery percentage uses the original voltage lookup and is an estimate.

Do not share the same `ble_client` with another component, or let a phone, old gateway or proxy
maintain a competing connection to the same puck. Multiple pucks may be configured
within ESPHome's BLE connection and memory limits.

## Local automations

Actions return after accepting the request. Observe `on_event` for the actual
result; action acceptance does not mean the puck has transmitted.

```yaml
script:
  - id: learn_power
    then:
      - meizu_ble.learn:
          id: puck
          request_id: !lambda 'return "learn-" + std::to_string(millis());'
          device: tv
          timeout: 15
```

Keep that request ID to call `meizu_ble.cancel`. Use a new ID for each intentional
operation. The component rejects recent completed IDs and does not restart an
active transaction on a repeated ID. Duplicate suppression is in memory, not a
durable cross-reboot transaction log.

`meizu_ble.send` takes `id`, `request_id`, a nine-byte hexadecimal `key`, and optional
complete hexadecimal `ir_code`. A cache miss without a complete code fails explicitly.
The component sends 15-byte data fragments, handles the puck's missing-fragment
requests, and confirms only a cache `0x04` or final upload `0x02` acknowledgement.
It never retries a whole IR command automatically after an ambiguous timeout.
The HA transport rejects concurrent sends rather than holding a second command
behind the first and transmitting it much later.

`on_event` receives `const MeizuEvent &event`. Relevant fields:

| Field | Meaning |
| --- | --- |
| `request_id`, `operation` | Correlation ID and `poll`, `send`, `learn` or `status` |
| `phase` | `queued`, `connecting`, `ready`, `receiving`, `complete`, `failed`, `cancelled` |
| `status` | `ok` or an explicit failure reason |
| `confirmed`, `cached`, `ack` | Actual Bluetooth send acknowledgement |
| `received`, `length`, `timeout_ms` | Capture progress; countdown starts at real `0x07` readiness |
| `raw` | Complete capture, also retained if conversion reports `unsupported_capture` |
| `ir_code`, `key`, `carrier_hz` | Validated conversion for successful learning; rejected captures have no send code |

Learning never uploads or transmits a captured command. A separate send action is
required for a physical test. Captures are bounded to 1500 bytes; direct send payloads
are bounded to 3800 bytes, and capture conversion supports eight timing pairs. Unsupported formats fail. Callbacks
can use a learned code locally; this component does not implement a persistent
named-command database on the ESP32. The HA adapter below provides that database.

## Home Assistant learning wizard and command store

Use the **1.5.0 integration source from this repository**. Copy
`custom_components/meizu_remoter_gateway/` to HA and restart after backing up the
previous integration directory. Add the ESPHome node through HA's standard ESPHome
integration first.

The complete YAML example enables:

```yaml
api:
  custom_services: true
  homeassistant_services: true

meizu_ble:
  - id: puck
    ble_client_id: puck_client
    api_service: meizu_puck
```

Keep the encryption and Wi-Fi/OTA secrets from the complete example. The component
registers one native action, typically `esphome.meizu_gateway_meizu_puck`; inspect
the actual name in HA's developer tools. Its fields are `request_id`, `operation`,
`key`, `ir_code`, `timeout` (seconds), and `device` (command namespace).

In the Meizu integration, choose **ESPHome BLE component**, select the ESPHome device,
enter that action name without `esphome.`, `api_source: meizu_puck`, and the puck MAC.
The form verifies a read-only status roundtrip; it never emits IR. It checks the
origin device ID, source, BLE address, protocol version and unique request ID.

The existing Configure wizard and standard `remote.learn_command`,
`remote.send_command` and `remote.delete_command` remain available. HA and the ESP32
independently convert a learned capture and must agree before HA saves it. Save
failures retain the previous key. The HA transport waits for correlated
`esphome.meizu_ble` result events rather than treating a service call as success.

ESPHome mode creates only the additional remote entity. Sensors and firmware
management use the standard ESPHome integration, avoiding duplicate sensor/update
entities. BLE addresses and polling intervals are configured in YAML; the legacy
TCP `bind` and `set_update_interval` actions explicitly report that these settings
are managed by ESPHome.

The remote entity's availability describes the ESPHome control path. A stale or
failed sensor poll does not prevent a deliberate manual send from attempting to
reconnect the puck; success still requires the actual Bluetooth acknowledgement.
It does not mark any sensor sample as fresh. HA waits up to 70 seconds to cover the
component's 15-second start deadline and 45-second active-send bound.

To migrate a single existing puck, use the existing Meizu integration entry's
**Reconfigure → ESPHome** path. Selecting the same puck MAC retains the config-entry
ID, serial identity and HA command Store. The existing remote entity keeps its
identity when the same puck is selected. Sensor and firmware-update entities move
to the standard ESPHome integration, so review dashboard/automation entity references
before switching firmware. One adapter entry addresses one puck;
reconfiguring a legacy multi-puck gateway does not automatically migrate all of its
pucks. Back up its Store and plan separate entries before migrating that setup.
No migration or device deployment is performed by compiling this repository.

## Verification and physical migration

```sh
python -B -m unittest discover -s tests/esphome -v
python -B -m unittest discover -s tests/meizu_remoter_gateway -v
esphome compile tests/esphome/compile.yaml
esphome compile tests/esphome/compile-c3.yaml
esphome compile tests/esphome/standalone.yaml
```

Use a Python environment with `esphome==2026.10.2`. Host tests run production codec,
protocol and GATT adapter code under ASan/UBSan with simulated BLE callbacks; no
test connects to a real puck or sends IR. The compile YAMLs contain synthetic MACs
and example network settings and are not deployment configurations.
Linux host tests also require a C++17 compiler and OpenSSL development headers
(`libssl-dev` on Debian/Ubuntu); macOS tests use the system CommonCrypto implementation.

Before installing onto an existing gateway, preserve its full Flash and read the
actual bootloader, partition table and OTA selector. Build for the correct ESP32
variant with private Wi-Fi/API/OTA secrets. The standalone gateway's OTA accepts
its own firmware format; it is not an ESPHome migration installer. Do not assume
the two firmware families share credentials, bindings, partitions or rollback
semantics. First use a spare board or a deliberate USB migration with a verified
restore path.

The hardware trial rejected an unsupported 45-byte capture without saving it;
another attempt timed out, and a subsequent 40-byte capture succeeded. Do not
weaken capture validation to save rejected data. Zero/unchanged samples and packet
faults have host regressions but were not deliberately induced on hardware.
Long air-conditioner codes, other hardware, long-term reliability and restoring
the original full Flash remain unverified. Bluetooth acknowledgement alone cannot
verify the appliance state; the HDMI trial included human observation.

The component and its codec are MIT licensed; see [LICENSE](LICENSE) and
[repository attribution](../../NOTICE.md). ESPHome and other build dependencies
retain their own licenses; the ESPHome C++ runtime is GPLv3. This source license
does not relicense a compiled firmware image or bundled dependencies.
