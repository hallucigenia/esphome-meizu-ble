# ESPHome Meizu BLE

**English** | [简体中文](README.zh-CN.md)

Connect a Meizu R16 infrared puck to Home Assistant through an ESP32 running
ESPHome. Read its climate sensors, learn infrared buttons, and send saved commands
over your local network.

This project provides an **ESPHome external component** and an optional
**Home Assistant adapter** for guided learning and a named-command library.
ESPHome handles Bluetooth, Wi-Fi, encrypted native API and OTA updates.

**Experimental release:** component **0.1.0**, HA adapter **1.5.0**.
[Download v0.1.0](https://github.com/hallucigenia/esphome-meizu-ble/releases/tag/v0.1.0)

## Features

- **Sensor readings:** temperature, humidity, estimated battery level, battery
  voltage, Bluetooth signal, sample age and measurement availability.
- **Infrared control:** send saved commands and wait for the puck's Bluetooth
  acknowledgement; cached commands can be uploaded again when needed.
- **Learning:** capture and convert infrared buttons, with readiness feedback,
  a timeout and cancellation. The HA adapter adds a step-by-step wizard.
- **Local operation:** use ESPHome automations or HA remote actions. Runtime
  operation requires no vendor app, cloud service or HA-host Bluetooth adapter.

## Requirements

| Item | Supported or verified configuration |
| --- | --- |
| Puck | Meizu R16; one unit has been physically tested |
| ESP32 | ESP32-D0WD revision 1.0 with 4 MB Flash physically tested; ESP32-C3 build verified in CI |
| ESPHome | Tested on 2026.10.2 with ESP-IDF 5.5.6; ESP-IDF framework required |
| Home Assistant | Optional; adapter tested with HA 2026.11.1 |

Use a dedicated `ble_client` for each puck, with `auto_connect: false`. Keep other
phones or gateways from holding a competing Bluetooth connection to that puck.

## Install the ESPHome component

1. Start with the [complete ESP32 configuration](examples/esphome-meizu.yaml).
   It uses a local component path when cloned. If copying it into ESPHome Device
   Builder, replace its `external_components` section with the published source:

   ```yaml
   external_components:
     - source: github://hallucigenia/esphome-meizu-ble@v0.1.0
       components: [meizu_ble]
   ```

2. Add the four entries from [the secrets template](examples/secrets.example.yaml)
   to your private `secrets.yaml` beside the device configuration. Fill in your
   Wi-Fi credentials, a fresh 32-byte base64 API encryption key and an OTA password.

3. Replace the synthetic `02:00:00:00:00:01` MAC with your puck's Bluetooth MAC,
   and select your ESP32 board. Keep the ESP-IDF framework and
   `auto_connect: false` settings from the example.

4. Build and install through ESPHome. Sensor polling defaults to five minutes;
   the interval can be set to whole minutes from 1 to 30. Failed polls back off
   automatically, and availability/sample age help identify stale measurements.

When replacing another firmware family, first preserve a full Flash backup and
check its actual partition layout. Use a deliberate USB migration; the old
gateway's OTA format is different. Subsequent updates use ESPHome OTA.

## Add Home Assistant learning and saved commands

HA's standard ESPHome integration provides sensors and device firmware updates.
Install the optional adapter for the learning wizard and named commands:

1. Add the ESPHome node to Home Assistant.
2. Download `meizu_remoter_gateway-1.5.0.zip` from the
   [release page](https://github.com/hallucigenia/esphome-meizu-ble/releases/tag/v0.1.0),
   or copy [`custom_components/meizu_remoter_gateway/`](custom_components/meizu_remoter_gateway/).
   Place the component directory under HA's `/config/custom_components/`, then
   restart HA. Back up an existing installation before replacing its source.
3. Add **Meizu Bluetooth gateway**, choose **ESPHome BLE component**, and select
   the ESPHome device. With the complete example, enter:

   | Field | Example |
   | --- | --- |
   | ESPHome action | `meizu_gateway_meizu_puck`, without the `esphome.` prefix |
   | Component `api_service` | `meizu_puck` |
   | Puck BLE MAC | Your puck's actual Bluetooth MAC |

   Check the actual action name in HA Developer Tools. Setup verifies a read-only
   status roundtrip; it does not transmit infrared.

### Learn a button

Open the Meizu integration's **Configure** wizard and name the device and button.
Point the original remote at the center of the puck's top surface, less than
1 cm away. **Wait for the ready prompt, then press the button.**

You can also use a standard HA action. Replace the example entity ID with yours:

```yaml
action: remote.learn_command
target:
  entity_id: remote.meizu_puck
data:
  device: tv
  command: power
  command_type: ir
  timeout: 15
```

Learning saves a button after complete capture, matching MCU/HA conversions and
successful storage. Learning does not transmit; use the wizard's test action or
send the saved command separately. Closing the wizard cancels learning.

### Send or delete a saved button

```yaml
action: remote.send_command
target:
  entity_id: remote.meizu_puck
data:
  device: tv
  command: power
```

Use `remote.delete_command` with the same `device` and `command` to delete it.
The adapter waits for a matching Bluetooth acknowledgement before marking a send
confirmed. It does not automatically retry ambiguous timeouts. A puck response
does not establish the controlled appliance's current state.

## Migrate an existing Meizu installation

Keep a backup of the HA component directory and command store. Use the existing
entry's **Reconfigure → ESPHome** path and select the same puck MAC to retain its
remote identity and saved commands. The HA domain remains `meizu_remoter_gateway`.

Sensors and firmware-update entities move to the standard ESPHome integration;
review their dashboard and automation references. One adapter entry addresses one
puck, so plan separate entries when moving a legacy multi-puck gateway.

## Validation and current limits

Basic physical checks passed on one R16 and the ESP32 listed above: real sensor
samples, learning with an observed appliance response, cancellation, timeout,
native OTA and power recovery. Migration retained the existing remote and nine
saved commands.

Other puck models, long air-conditioner codes, long-term reliability and restoring
the original full Flash remain unverified. CI covers host regressions, real HA
event/service handling, and ESP32, ESP32-C3 and no-API builds.

## Documentation and development

- [Component reference](components/meizu_ble/README.md): all sensors, local
  actions, API events and protocol limits.
- [HA adapter guide (中文)](custom_components/meizu_remoter_gateway/README.md):
  learning, command storage and service details.
- [Contributing and tests](CONTRIBUTING.md): development environments and checks.

## License and acknowledgements

Repository source is [MIT licensed](LICENSE), retaining the copyright notices for
**George Zhao** and **hallucigenia**. Thank you to George Zhao for the
[original gateway and device/protocol groundwork](https://github.com/georgezhao2010/esp32_meizu_remoter_gateway)
and the [MIT-licensed HA integration](https://github.com/georgezhao2010/meizu_remoter_gateway).

ESPHome and other dependencies retain their own licenses. In particular, ESPHome's
C++ runtime is GPLv3; distributing a compiled firmware image requires complying
with its dependency licenses as well. See [NOTICE.md](NOTICE.md) for attribution
and license scope.
