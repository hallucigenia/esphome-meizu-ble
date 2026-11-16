# Development and validation

Use Python 3.12 for ESPHome 2026.10.2 and Python 3.14 for the real Home Assistant
2026.11.1 checks. The isolated HA tests also run under Python 3.11. Do not use
deployment configurations or real credentials as fixtures.

```sh
python -B -m unittest discover -s tests/meizu_remoter_gateway -v
python tools/package-ha-release.py --output release-ha
```

In an environment with `esphome==2026.10.2` installed:

```sh
python -B -m unittest discover -s tests/esphome -v
esphome compile tests/esphome/compile.yaml
esphome compile tests/esphome/compile-c3.yaml
esphome compile tests/esphome/standalone.yaml
```

The host tests require a C++17 compiler and run the production codec, protocol and
GATT adapter under ASan/UBSan. Linux also needs OpenSSL development headers
(`libssl-dev` on Debian/Ubuntu); macOS uses CommonCrypto. The test BLE interface is
simulated and does not connect to a puck or transmit infrared.

In a separate environment with `homeassistant==2026.11.1` installed:

```sh
python -B -m unittest discover -s tests/ha_core -v
```

These checks use the real HA EventBus and ServiceRegistry with asyncio debug
enabled, without connecting to an HA server or physical hardware. The baseline
contains 84 isolated HA tests, 10 ESPHome host/schema tests and 3 real HA checks.

The complete example uses private `secrets.yaml`; only the placeholder template is
source. Release archives are built deterministically from an explicit HA source
allowlist. Keep runtime command stores, Flash dumps and maintenance logs outside
the repository.
