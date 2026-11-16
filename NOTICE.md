# Attribution and license scope

## Repository source

The source files distributed in this repository are MIT licensed. The complete
license and copyright notices are in [LICENSE](LICENSE), with copies alongside
the ESPHome component and Home Assistant integration.

Copyright (c) 2021 George Zhao
Copyright (c) 2026 hallucigenia

The Home Assistant integration derives from
[George Zhao's MIT-licensed integration](https://github.com/georgezhao2010/meizu_remoter_gateway).
Its original copyright notice is retained. The new HA native API adapter,
learning codec, ESPHome component, examples, tests and packaging tools are
distributed under the same MIT terms. The C++ ET4007 codec is ported from the
Python HA codec included here.

The original
[ESP32 Meizu gateway project](https://github.com/georgezhao2010/esp32_meizu_remoter_gateway)
provided device and protocol groundwork. Its legacy standalone firmware has no
explicit project license; that firmware's implementation files, build settings,
prebuilt images and photographs are not included or relicensed here. The HA
adapter retains its MIT-licensed TCP compatibility path for existing installations.

The runtime does not load a proprietary APK, JNI library or emulated vendor
binary. Captured test waveforms use generic command names and contain no device
identity or credentials.

## External dependencies and compiled firmware

ESPHome, Home Assistant, ESP-IDF, cryptographic libraries and toolchains are
external dependencies, not relicensed by this repository. In the tested
[ESPHome 2026.10.2 license](https://github.com/esphome/esphome/blob/2026.10.2/LICENSE),
C++/runtime files are GPLv3, while Python and other project files are MIT.

MIT-licensed component source can be combined with GPLv3 software, but distributing
the resulting firmware still requires complying with GPLv3 and the other bundled
dependencies' license terms. This repository's releases provide source and the
source-only HA adapter archive; they do not provide a configured or precompiled
ESPHome firmware image.

## Acknowledgements

Thank you to George Zhao for the original gateway and HA integration, and to the
ESPHome and Home Assistant contributors for the frameworks used by this project.
Preserve the license text and applicable copyright notices when redistributing
repository code; an acknowledgement alone does not replace those notices.
