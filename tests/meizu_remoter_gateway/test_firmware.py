"""Release trust, bounded image metadata and cross-language OTA authorization."""
import hashlib
import hmac
import importlib.util
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2] / "custom_components/meizu_remoter_gateway"
SPEC = importlib.util.spec_from_file_location("meizu_firmware_test", ROOT / "firmware.py")
firmware = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(firmware)


def manifest():
    return {"schema": 1, "board": firmware.BOARD, "version": "0.7.1", "min_integration": "1.4.0",
            "builds": [{"chipFamily": "ESP32", "ota": {
                "path": firmware.RELEASE_ROOT + "/download/v0.7.1/firmware.ota.bin",
                "sha256": "b" * 64, "size": 1300000}}]}


class FirmwareTests(unittest.TestCase):
    def test_release_accepts_only_the_matching_project_layout_and_pinned_asset(self):
        result = firmware.parse_manifest(manifest())
        self.assertEqual(result["size"], 1300000)
        self.assertEqual(result["version"], "0.7.1")
        for key, value in (("board", "esp32-c3"), ("schema", 2), ("min_integration", "99.0.0"), ("version", "../latest")):
            changed = manifest(); changed[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError): firmware.parse_manifest(changed)
        for key, value in (("path", "http://evil.invalid/app.bin"), ("path", firmware.RELEASE_ROOT + "/latest/download/firmware.ota.bin"),
                           ("size", firmware.SLOT_SIZE + 1), ("size", True), ("sha256", "invalid")):
            changed = manifest(); changed["builds"][0]["ota"][key] = value
            with self.subTest(key=key, value=value), self.assertRaises(ValueError): firmware.parse_manifest(changed)

    def test_wire_authorization_matches_the_firmware_canonical_contract(self):
        image = {"id": "a" * 32, "version": "0.7.1", "size": 1300000, "sha256": "b" * 64,
                 "url": firmware.RELEASE_ROOT + "/download/v0.7.1/firmware.ota.bin", "ts": 1794750000}
        message = "meizu-ota-v1\nstart\nABC123\n" + "c" * 64 + "\n" + "a" * 32 + "\n0.7.1\n1300000\n" + "b" * 64 + "\n" + image["url"] + "\n1794750000"
        expected = hmac.new(bytes(range(32)), message.encode(), hashlib.sha256).hexdigest()
        key = bytes(range(32)).hex()
        self.assertEqual(firmware.authorization(key, "start", "ABC123", "c" * 64, image), expected)
        for changed in ({**image, "size": 1300001}, {**image, "ts": 1794750001}, {**image, "version": "0.7.2"}):
            self.assertNotEqual(firmware.authorization(key, "start", "ABC123", "c" * 64, changed), expected)
        self.assertNotEqual(firmware.authorization(key, "cancel", "ABC123", "c" * 64, image), expected)
        self.assertNotEqual(firmware.authorization(key, "start", "OTHER", "c" * 64, image), expected)
        image["url"] += "\n" + "injected"
        with self.assertRaises(ValueError): firmware.authorization(key, "start", "ABC123", "c" * 64, image)

    def test_full_authenticated_start_still_fits_the_existing_tcp_receive_limit(self):
        image = {**firmware.parse_manifest(manifest()), "id": "a" * 32, "ts": 1794750000}
        image = {key: image[key] for key in ("version", "url", "size", "sha256", "id", "ts")}
        image["auth"] = firmware.authorization("00" * 32, "start", "ABC123", "c" * 64, image)
        self.assertLessEqual(len(json.dumps({"type": "ota_start", "data": image}, separators=(",", ":")).encode()), 511)
        for key in ("", "g" * 64, "a" * 63):
            with self.assertRaises(ValueError): firmware.key_bytes(key)
