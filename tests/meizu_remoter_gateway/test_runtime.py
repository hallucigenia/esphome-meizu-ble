"""Freshness and command behavior without importing or connecting to HA."""

import ast
from copy import deepcopy
import json
import asyncio
from pathlib import Path
import time
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from test_client import client
from test_codec import codec, SAMPLES

FILE = Path(__file__).resolve().parents[2] / "custom_components/meizu_remoter_gateway/__init__.py"
TREE = ast.parse(FILE.read_text())
RUNTIME = next(node for node in TREE.body if isinstance(node, ast.ClassDef) and node.name == "GatewayRuntime")
NAMESPACE = {"asyncio": asyncio, "time": time, "DOMAIN": "meizu_remoter_gateway", "PLATFORMS": ["sensor", "remote", "update"], "callback": lambda fn: fn,
             "GatewayClient": client.GatewayClient, "normalize_address": client.normalize_address,
             "ir_message": client.ir_message, "HomeAssistantError": RuntimeError, "ServiceValidationError": ValueError,
             "measurement": client.measurement,
             "deepcopy": deepcopy, "json": json, "convert_capture": codec.convert_capture,
             "storage": SimpleNamespace(Store=lambda *args: SimpleNamespace(async_save=AsyncMock()))}
exec(compile(ast.Module(body=[RUNTIME], type_ignores=[]), str(FILE), "exec"), NAMESPACE)
GatewayRuntime = NAMESPACE["GatewayRuntime"]
ADDRESS = "02:00:00:00:ab:02"


class RuntimeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        entry = SimpleNamespace(data={"host": "127.0.0.1", "port": 8266, "serialno": "ABC123"}, entry_id="test")
        self.runtime = GatewayRuntime(None, entry)
        self.runtime.client = SimpleNamespace(connected=True, send=AsyncMock(), send_ir=AsyncMock(return_value={"confirmed": True, "ack": 4}))

    def report(self, kind="update", **changes):
        data = {"device": ADDRESS, "available": 1, "status": {"temperature": 0, "humidity": 0}, **changes}
        self.runtime.message({"type": kind, "data": {"devices": [data]} if kind == "devices" else data})

    def test_cached_device_list_does_not_masquerade_as_new_measurement(self):
        self.report("devices")
        self.assertFalse(self.runtime.available(ADDRESS))
        self.assertTrue(self.runtime.available(ADDRESS, fresh=False))
        self.assertIsNone(self.runtime.devices[ADDRESS]["last_measurement"])

    def test_esphome_remote_can_attempt_recovery_without_fabricating_a_sensor_sample(self):
        self.runtime.transport = "esphome"
        self.report("devices", available=0)
        self.assertTrue(self.runtime.available(ADDRESS, fresh=False))
        self.assertFalse(self.runtime.available(ADDRESS))
        self.assertIsNone(self.runtime.devices[ADDRESS]["last_measurement"])
        self.runtime.client.connected = False
        self.assertFalse(self.runtime.available(ADDRESS, fresh=False))

    def test_short_octets_from_real_firmware_create_one_canonical_device(self):
        self.report(device="02:00:00:00:ab:0")
        self.report(device="02:00:00:00:ab:00")
        self.assertEqual(list(self.runtime.devices), ["02:00:00:00:ab:00"])

    def test_real_unchanged_zero_measurements_refresh_freshness(self):
        with patch.object(time, "time", return_value=1000):
            self.report()
        with patch.object(time, "time", return_value=1400):
            self.report()
            self.assertTrue(self.runtime.available(ADDRESS))
        self.assertEqual(self.runtime.devices[ADDRESS]["last_measurement"], 1400)
        self.assertEqual(self.runtime.devices[ADDRESS]["status"]["temperature"], 0)

    def test_old_measurements_expire_even_when_tcp_is_connected(self):
        with patch.object(time, "time", return_value=1000):
            self.report()
        with patch.object(time, "time", return_value=1691):
            self.assertFalse(self.runtime.available(ADDRESS))

    def test_cached_list_cannot_extend_an_existing_sample(self):
        with patch.object(time, "time", return_value=1000):
            self.report()
        with patch.object(time, "time", return_value=1400):
            self.report("devices")
        self.assertEqual(self.runtime.devices[ADDRESS]["last_measurement"], 1000)

    def test_new_firmware_cache_keeps_actual_sample_age_across_reconnect(self):
        self.runtime.client.capabilities = {"measurement_age_v1"}
        with patch.object(time, "time", return_value=1000):
            self.report("devices", measurement_age_ms=20000)
            self.assertTrue(self.runtime.available(ADDRESS))
        self.assertEqual(self.runtime.devices[ADDRESS]["last_measurement"], 980)
        with patch.object(time, "time", return_value=1020):
            self.report("devices", measurement_age_ms=40000)
        self.assertEqual(self.runtime.devices[ADDRESS]["last_measurement"], 980)

    def test_invalid_cached_age_never_becomes_a_fresh_sample(self):
        self.runtime.client.capabilities = {"measurement_age_v1"}
        with patch.object(time, "time", return_value=1000):
            for age in (None, True, -1, "12", float("inf"), float("nan"), 1000001):
                self.report("devices", measurement_age_ms=age)
                self.assertIsNone(self.runtime.devices[ADDRESS]["last_measurement"])
                self.assertFalse(self.runtime.available(ADDRESS))

    def test_offline_binding_survives_a_list_without_measurements(self):
        self.runtime.message({"type": "devices", "data": {"devices": [{"device": ADDRESS, "available": 0, "status": {}}]}})
        self.assertIn(ADDRESS, self.runtime.devices)
        self.assertFalse(self.runtime.available(ADDRESS, fresh=False))
        self.assertIsNone(self.runtime.devices[ADDRESS]["last_measurement"])

    def test_failed_poll_marks_unavailable_and_preserves_last_value(self):
        self.report()
        self.runtime.message({"type": "update", "data": {"device": ADDRESS, "available": 0}})
        self.assertFalse(self.runtime.available(ADDRESS))
        self.assertEqual(self.runtime.devices[ADDRESS]["status"]["humidity"], 0)

    async def test_unknown_hdmi_key_does_not_emit_any_infrared(self):
        self.report()
        with self.assertRaisesRegex(ValueError, "尚未采集"):
            await self.runtime.send_command(ADDRESS, "hdmi_switch", "input_1")
        self.runtime.client.send.assert_not_called()

    async def test_saving_a_key_does_not_send_and_named_send_uses_stored_payload(self):
        self.report()
        packet = client.ir_message(ADDRESS, "aabb", "0011")
        await self.runtime.save_command(ADDRESS, "hdmi_switch", "input_1", packet["data"])
        self.runtime.store.async_save.assert_awaited_once()
        self.runtime.client.send.assert_not_called()
        await self.runtime.send_command(ADDRESS, "hdmi_switch", "input_1")
        self.runtime.client.send_ir.assert_awaited_once_with(packet)
        self.assertTrue(self.runtime.last_ir_results[ADDRESS]["confirmed"])

    async def test_failed_ack_clears_previous_success_and_raises(self):
        self.report()
        await self.runtime.save_command(ADDRESS, "hdmi_switch", "input_1", {"key": "aabb"})
        self.runtime.last_ir_results[ADDRESS] = {"confirmed": True}
        self.runtime.client.send_ir.side_effect = OSError("ack_timeout")
        with self.assertRaisesRegex(RuntimeError, "ack_timeout"):
            await self.runtime.send_command(ADDRESS, "hdmi_switch", "input_1")
        self.assertFalse(self.runtime.last_ir_results[ADDRESS]["confirmed"])

    async def test_disconnected_gateway_cannot_send_a_saved_key(self):
        self.report()
        await self.runtime.save_command(ADDRESS, "hdmi_switch", "input_1", {"key": "aabb"})
        self.runtime.client.connected = False
        with self.assertRaisesRegex(RuntimeError, "不可用"):
            await self.runtime.send_command(ADDRESS, "hdmi_switch", "input_1")
        self.runtime.client.send.assert_not_called()

    async def test_native_learning_saves_full_code_and_raw_without_emitting(self):
        self.report()
        self.runtime.client.learn_ir = AsyncMock(return_value=bytes.fromhex(SAMPLES[0]["raw"]))
        await self.runtime.learn_command(ADDRESS, "hdmi_switch", "input_1")
        saved = self.runtime.commands[ADDRESS]["hdmi_switch"]["input_1"]
        self.assertEqual(saved["ir_code"], SAMPLES[0]["code"])
        self.assertEqual(saved["raw"], SAMPLES[0]["raw"])
        self.runtime.client.send_ir.assert_not_called()
        self.assertIsNone(self.runtime.learning_task)
        self.assertEqual(self.runtime.learning["phase"], "saved")

    async def test_incomplete_capture_and_disk_failure_preserve_old_key(self):
        self.report()
        await self.runtime.save_command(ADDRESS, "tv", "power", {"key": "aabb"})
        self.runtime.client.learn_ir = AsyncMock(return_value=b"bad")
        with self.assertRaises(RuntimeError):
            await self.runtime.learn_command(ADDRESS, "tv", "power")
        self.assertEqual(self.runtime.commands[ADDRESS]["tv"]["power"], {"key": "aabb"})
        self.runtime.client.learn_ir.return_value = bytes.fromhex(SAMPLES[0]["raw"])
        self.runtime.store.async_save.side_effect = OSError("disk full")
        with self.assertRaisesRegex(RuntimeError, "disk full"):
            await self.runtime.learn_command(ADDRESS, "tv", "power")
        self.assertEqual(self.runtime.commands[ADDRESS]["tv"]["power"], {"key": "aabb"})

    async def test_cancellation_never_replaces_the_existing_key(self):
        self.report()
        await self.runtime.save_command(ADDRESS, "tv", "power", {"key": "aabb"})
        self.runtime.client.learn_ir = AsyncMock(side_effect=asyncio.CancelledError)
        with self.assertRaises(asyncio.CancelledError):
            await self.runtime.learn_command(ADDRESS, "tv", "power")
        self.assertEqual(self.runtime.commands[ADDRESS]["tv"]["power"], {"key": "aabb"})
        self.assertEqual(self.runtime.learning["phase"], "cancelled")
        self.assertIsNone(self.runtime.learning_task)


if __name__ == "__main__":
    unittest.main()
