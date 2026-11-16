"""Bounded release metadata and the authenticated OTA wire format; no HA IO."""
from __future__ import annotations

import hashlib
import hmac
import re

VERSION = "1.4.0"
BOARD = "esp32-4m-ab-v1"
SLOT_SIZE = 0x1C0000
MANIFEST_URL = "https://github.com/hallucigenia/esp32_meizu_remoter_gateway/releases/latest/download/manifest.json"
RELEASE_ROOT = "https://github.com/hallucigenia/esp32_meizu_remoter_gateway/releases"


def stable_version(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d+\.\d+\.\d+", value) or len(value) > 31:
        raise ValueError("Invalid firmware version")
    return tuple(int(part) for part in value.split("."))


def parse_manifest(value):
    if not isinstance(value, dict) or value.get("schema") != 1 or value.get("board") != BOARD:
        raise ValueError("Firmware is for a different gateway or partition layout")
    version = value.get("version")
    stable_version(version)
    minimum = value.get("min_integration", "1.4.0")
    if stable_version(minimum) > stable_version(VERSION):
        raise ValueError("Update the Home Assistant integration before this firmware")
    builds = value.get("builds")
    if not isinstance(builds, list) or len(builds) != 1 or not isinstance(builds[0], dict) or builds[0].get("chipFamily") != "ESP32":
        raise ValueError("Firmware build must target ESP32")
    image = builds[0].get("ota")
    if not isinstance(image, dict):
        raise ValueError("Missing OTA image")
    expected = f"{RELEASE_ROOT}/download/v{version}/firmware.ota.bin"
    if image.get("path") != expected:
        raise ValueError("Untrusted firmware URL")
    size = image.get("size")
    if type(size) is not int or not 1024 <= size <= SLOT_SIZE:
        raise ValueError("Firmware does not fit the gateway application slot")
    digest = image.get("sha256")
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError("Missing firmware SHA-256")
    return {"version": version, "url": expected, "size": size, "sha256": digest,
            "summary": str(image.get("summary", ""))[:4000], "release_url": f"{RELEASE_ROOT}/tag/v{version}"}


def key_bytes(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", value):
        raise ValueError("请在网关重新配置中填写 USB 串口读取的 64 位 OTA 密钥")
    return bytes.fromhex(value)


def authorization(key, operation, serial, nonce, image):
    if operation not in ("start", "cancel", "check") or not re.fullmatch(r"[0-9a-f]{64}", nonce):
        raise ValueError("Invalid OTA challenge")
    fields = ("meizu-ota-v1", operation, serial, nonce, image["id"], image.get("version", ""),
              str(image.get("size", 0)), image.get("sha256", ""), image.get("url", ""), str(image["ts"]))
    if any("\n" in field or "\r" in field for field in fields):
        raise ValueError("Invalid OTA authorization field")
    return hmac.new(key_bytes(key), "\n".join(fields).encode(), hashlib.sha256).hexdigest()
