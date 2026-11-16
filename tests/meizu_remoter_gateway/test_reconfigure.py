"""Retain a paired key across legacy recovery, while validating new OTA credentials."""
import ast
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

FILE = Path(__file__).resolve().parents[2] / "custom_components/meizu_remoter_gateway/config_flow.py"
TREE = ast.parse(FILE.read_text())
FLOW = next(node for node in TREE.body if isinstance(node, ast.ClassDef) and node.name == "MeizuGatewayFlow")


class Base:
    def __init_subclass__(cls, **kwargs): pass
    def _get_reconfigure_entry(self): return self.entry
    def async_update_reload_and_abort(self, entry, data_updates): return {"type": "abort", "updates": data_updates}
    def async_abort(self, reason): return {"type": "abort", "reason": reason}


class ReconfigureTests(unittest.IsolatedAsyncioTestCase):
    async def check(self, capabilities, entered):
        client = SimpleNamespace(capabilities=capabilities, connect=AsyncMock(return_value={"serialno": "ABC123"}), close=AsyncMock())
        verify = AsyncMock()
        namespace = {"config_entries": SimpleNamespace(ConfigFlow=Base), "callback": lambda fn: fn,
                     "DOMAIN": "meizu_remoter_gateway", "GatewayClient": lambda *args: client, "verify_ota_key": verify}
        exec(compile(ast.Module(body=[FLOW], type_ignores=[]), str(FILE), "exec"), namespace)
        flow = namespace["MeizuGatewayFlow"]()
        flow.entry = SimpleNamespace(data={"host": "old", "port": 8266, "serialno": "ABC123", "ota_key": "11" * 32})
        result = await flow.async_step_reconfigure({"host": "new", "port": 8266, **({"ota_key": entered} if entered is not None else {})})
        client.close.assert_awaited_once()
        return result, verify

    async def test_legacy_firmware_can_change_address_without_losing_its_saved_ota_key(self):
        result, verify = await self.check(set(), "")
        self.assertEqual(result["updates"]["host"], "new")
        self.assertEqual(result["updates"]["ota_key"], "11" * 32)
        verify.assert_not_awaited()

    async def test_ota_firmware_authenticates_the_retained_key(self):
        result, verify = await self.check({"ota_pull_v1"}, "")
        self.assertEqual(result["updates"]["ota_key"], "11" * 32)
        self.assertEqual(verify.await_args.args[1:], ("ABC123", "11" * 32))
