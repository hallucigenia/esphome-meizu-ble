"""OTA upload acknowledgement is not success; verify boot and recover lost replies."""
import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace
import time
import unittest
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from test_firmware import firmware, manifest

FILE = Path(__file__).resolve().parents[2] / "custom_components/meizu_remoter_gateway/update.py"
TREE = ast.parse(FILE.read_text())
UPDATE = next(node for node in TREE.body if isinstance(node, ast.ClassDef) and node.name == "MeizuFirmwareUpdate")


class CoordinatorBase:
    def __init__(self, coordinator): self.coordinator = coordinator
    def async_write_ha_state(self): pass


class UpdateBase: pass
class HAError(Exception): pass


NS = {"CoordinatorEntity": CoordinatorBase, "UpdateEntity": UpdateBase,
      "UpdateDeviceClass": SimpleNamespace(FIRMWARE="firmware"),
      "UpdateEntityFeature": SimpleNamespace(INSTALL=1, PROGRESS=2, RELEASE_NOTES=4),
      "storage": SimpleNamespace(Store=lambda *args: SimpleNamespace(async_save=AsyncMock())),
      "DOMAIN": "meizu_remoter_gateway", "HomeAssistantError": HAError,
      "authorization": firmware.authorization, "key_bytes": firmware.key_bytes,
      "time": time, "asyncio": asyncio, "uuid4": uuid4,
      "_LOGGER": SimpleNamespace(warning=lambda *args: None)}
exec(compile(ast.Module(body=[UPDATE], type_ignores=[]), str(FILE), "exec"), NS)
Update = NS["MeizuFirmwareUpdate"]


class UpdateTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.image = None
        self.starts = []
        self.lost_reply = False
        self.reject = None
        self.phases = ["downloading", "verifying_boot", "complete"]
        self.entry = SimpleNamespace(data={"ota_key": bytes(range(32)).hex()}, entry_id="test")
        client = SimpleNamespace(connected=True, capabilities={"ota_pull_v1"}, ir_lock=asyncio.Lock(), ota_lock=asyncio.Lock(), request=AsyncMock(side_effect=self.request))
        client.ota_status = self.status
        self.runtime = SimpleNamespace(hass=None, serial="ABC123", version="0.7.0", ota_state={}, client=client, learning_task=None)
        self.entity = Update(SimpleNamespace(data=firmware.parse_manifest(manifest())), self.runtime, self.entry)

    async def status(self):
        return await self.request("ota_status")

    async def request(self, kind, data=None):
        if kind == "ota_challenge": return {"data": {"nonce": "c" * 64}}
        if kind == "ota_start":
            self.image = dict(data); self.starts.append(data)
            expected = firmware.authorization(self.entry.data["ota_key"], "start", "ABC123", "c" * 64, data)
            self.assertEqual(data["auth"], expected)
            if self.lost_reply: raise ConnectionError("ack lost after acceptance")
            return {"data": {"accepted": not self.reject, "status": self.reject or "accepted"}}
        phase = self.phases.pop(0)
        self.assertTrue(self.entity.in_progress)
        return {"data": {"id": self.image["id"], "phase": phase, "size": self.image["size"], "written": self.image["size"],
                         "confirmed": phase == "complete", "running_version": "0.7.1" if phase == "complete" else "0.7.0",
                         "sha256": self.image["sha256"], "error": "sha256_mismatch" if phase == "failed" else ""}}

    async def install(self):
        with patch.object(asyncio, "sleep", AsyncMock()):
            await self.entity.async_install(None, False)

    async def test_download_and_boot_verification_must_both_finish(self):
        await self.install()
        self.assertEqual(self.runtime.version, "0.7.1")
        self.assertEqual(len(self.starts), 1)
        self.assertFalse(self.entity.in_progress)
        self.assertFalse(self.entity._resumed)
        self.assertIsNone(self.entity.pending)
        self.assertEqual(self.entity.store.async_save.await_args.args, (None,))

    async def test_lost_start_ack_is_queried_without_a_second_flash(self):
        self.lost_reply = True
        await self.install()
        self.assertEqual(len(self.starts), 1)
        self.assertEqual(self.runtime.version, "0.7.1")

    async def test_failure_is_terminal_and_keeps_installed_version(self):
        self.phases = ["failed"]
        with self.assertRaisesRegex(HAError, "sha256_mismatch"): await self.install()
        self.assertEqual(self.runtime.version, "0.7.0")
        self.assertIsNone(self.entity.pending)

    async def test_rejected_start_clears_pending_and_never_polls_a_false_success(self):
        self.reject = "authentication_failed"
        with self.assertRaisesRegex(HAError, "authentication_failed"): await self.install()
        self.assertIsNone(self.entity.pending)
        self.assertEqual(self.phases, ["downloading", "verifying_boot", "complete"])

    async def test_ha_restart_resumes_monitoring_without_starting_again(self):
        self.image = {**firmware.parse_manifest(manifest()), "id": "a" * 32, "ts": 1794750000}
        self.entity.pending = self.image
        self.entity._installing = True
        self.phases = ["complete"]
        await self.entity._resume()
        self.assertTrue(self.entity._resumed)
        self.assertEqual(self.starts, [])
        self.assertIsNone(self.entity.pending)
        self.assertFalse(self.entity.in_progress)

    async def test_learning_and_missing_key_fail_before_network_io(self):
        self.entry.data["ota_key"] = "invalid"
        with self.assertRaises(HAError): await self.install()
        self.runtime.client.request.assert_not_called()
        self.entry.data["ota_key"] = "00" * 32
        self.runtime.learning_task = object()
        with self.assertRaises(HAError): await self.install()
        self.runtime.client.request.assert_not_called()
