"""Validate the component against the real pinned ESPHome YAML/codegen API."""
import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


@unittest.skipUnless(importlib.util.find_spec("esphome"), "Install pinned ESPHome to validate schemas")
class SchemaTests(unittest.TestCase):
    def setUp(self):
        from esphome import yaml_util
        self.yaml = yaml_util
        self.config = yaml_util.load_yaml(ROOT / "tests/esphome/compile.yaml")
        self.config["external_components"][0]["source"]["path"] = str(ROOT / "components")

    def validate(self, config, expected=None):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "test.yaml"
            path.write_text(self.yaml.dump(config))
            result = subprocess.run([sys.executable, "-m", "esphome", "config", str(path)], capture_output=True, text=True)
            output = result.stdout + result.stderr
            if expected is None:
                self.assertEqual(result.returncode, 0, output)
            else:
                self.assertNotEqual(result.returncode, 0, output)
                self.assertIn(expected, output)

    def test_sensor_actions_and_native_api_configuration_validate(self):
        self.validate(self.config)

    def test_battery_device_cannot_be_kept_permanently_connected(self):
        self.config["ble_client"][0]["auto_connect"] = True
        self.validate(self.config, "auto_connect: false")

    def test_shared_client_is_rejected_before_firmware_generation(self):
        second = {"id": "second_puck", "ble_client_id": "puck_client", "api_service": "meizu_second"}
        self.config["meizu_ble"].append(second)
        self.validate(self.config, "dedicated")

    def test_api_bridge_requires_explicit_framework_features(self):
        self.config["api"]["custom_services"] = False
        self.validate(self.config, "custom_services: true")

    def test_invalid_poll_interval_is_rejected(self):
        self.config["meizu_ble"][0]["update_interval"] = "10s"
        self.validate(self.config, "whole number of minutes")

    def test_standalone_component_needs_no_api_or_home_assistant(self):
        config = self.yaml.load_yaml(ROOT / "tests/esphome/standalone.yaml")
        config["external_components"][0]["source"]["path"] = str(ROOT / "components")
        self.validate(config)


if __name__ == "__main__":
    unittest.main()
