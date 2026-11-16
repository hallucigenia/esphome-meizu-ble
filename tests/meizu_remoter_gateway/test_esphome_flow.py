"""The ESPHome form verifies its peer before creating or migrating a config entry."""
import ast
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

from test_esphome_client import SETTINGS, module

FILE = Path(__file__).resolve().parents[2] / "custom_components/meizu_remoter_gateway/config_flow.py"
FLOW = next(n for n in ast.parse(FILE.read_text()).body if isinstance(n, ast.ClassDef) and n.name == "MeizuGatewayFlow")


class Base:
    def __init_subclass__(cls, **kwargs): pass
    def _get_reconfigure_entry(self): return self.entry
    async def async_set_unique_id(self, value): self.unique_id = value
    def _abort_if_unique_id_configured(self, **kwargs): pass
    def async_create_entry(self, **kwargs): return {"type": "create_entry", **kwargs}
    def async_update_reload_and_abort(self, entry, data_updates): return {"type": "abort", "entry": entry, "updates": data_updates}
    def async_show_form(self, **kwargs): return {"type": "form", **kwargs}


class ESPHomeFlowTests(unittest.IsolatedAsyncioTestCase):
    def make_flow(self, error=None):
        peer = SimpleNamespace(connect=AsyncMock(side_effect=error), close=AsyncMock())
        namespace = {"config_entries": SimpleNamespace(ConfigFlow=Base), "DOMAIN": "meizu_remoter_gateway", "callback": lambda fn: fn,
                     "normalize_address": module.normalize_address, "ESPHomeGatewayClient": lambda *args: peer,
                     "vol": SimpleNamespace(Required=lambda name, **kwargs: name, Schema=lambda value: value),
                     "selector": SimpleNamespace(DeviceSelector=lambda value: value, DeviceSelectorConfig=lambda **kwargs: kwargs)}
        exec(compile(ast.Module(body=[FLOW], type_ignores=[]), str(FILE), "exec"), namespace)
        flow = namespace["MeizuGatewayFlow"]()
        flow.hass = object()
        flow.context = {}
        return flow, peer

    async def test_new_entry_is_created_only_after_status_roundtrip(self):
        flow, peer = self.make_flow()
        result = await flow.async_step_esphome(SETTINGS)
        self.assertEqual(result["type"], "create_entry")
        self.assertEqual(result["data"]["transport"], "esphome")
        self.assertTrue(result["data"]["serialno"].startswith("ESP_"))
        peer.connect.assert_awaited_once()
        peer.close.assert_awaited_once()

    async def test_reconfigure_retains_the_entry_id_and_serial_for_existing_command_store(self):
        flow, peer = self.make_flow()
        flow.context = {"source": "reconfigure"}
        flow.entry = SimpleNamespace(entry_id="existing-store-entry", data={"serialno": "ABC123", "host": "old.invalid", "port": 8266})
        result = await flow.async_step_esphome(SETTINGS)
        self.assertIs(result["entry"], flow.entry)
        self.assertEqual(result["updates"]["serialno"], "ABC123")
        self.assertEqual(result["updates"]["address"], SETTINGS["address"])
        self.assertEqual(flow.entry.data["host"], "old.invalid")
        peer.connect.assert_awaited_once()

    async def test_failed_peer_check_preserves_previous_configuration(self):
        flow, peer = self.make_flow(OSError("offline"))
        flow.context = {"source": "reconfigure"}
        flow.entry = SimpleNamespace(data={"serialno": "ABC123", "host": "old.invalid", "port": 8266})
        result = await flow.async_step_esphome(SETTINGS)
        self.assertEqual(result["type"], "form")
        self.assertEqual(result["errors"], {"base": "cannot_connect_esphome"})
        self.assertNotIn("transport", flow.entry.data)
        peer.close.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
