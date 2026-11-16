"""Wizard behavior: ready gating, replace consent and lifecycle cancellation."""
import ast
import asyncio
import math
from pathlib import Path
import time
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

FILE = Path(__file__).resolve().parents[2] / "custom_components/meizu_remoter_gateway/config_flow.py"
TREE = ast.parse(FILE.read_text())
FLOW = next(node for node in TREE.body if isinstance(node, ast.ClassDef) and node.name == "MeizuLearningFlow")


class Base:
    def async_show_form(self, **kwargs): return {"type": "form", **kwargs}
    def async_show_progress(self, **kwargs): return {"type": "progress", **kwargs}
    def async_show_progress_done(self, **kwargs): return {"type": "progress_done", **kwargs}
    def async_abort(self, **kwargs): return {"type": "abort", **kwargs}
    def async_create_entry(self, **kwargs): return {"type": "create_entry", **kwargs}
    def async_notify_flow_changed(self): pass
    def async_update_progress(self, value): pass


NS = {"asyncio": asyncio, "math": math, "time": time, "callback": lambda fn: fn,
      "HomeAssistantError": RuntimeError, "config_entries": SimpleNamespace(OptionsFlow=Base),
      "vol": SimpleNamespace(Required=lambda name, **_: name, Optional=lambda name, **_: name, Schema=lambda x: x),
      "selector": SimpleNamespace(SelectSelector=lambda x: x, SelectSelectorConfig=lambda **x: x)}
exec(compile(ast.Module(body=[FLOW], type_ignores=[]), str(FILE), "exec"), NS)
Flow = NS["MeizuLearningFlow"]
ADDRESS = "02:00:00:00:ab:01"


class FlowTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.runtime = SimpleNamespace(client=SimpleNamespace(connected=True, capabilities={"learn_v1"}),
            devices={ADDRESS: {}}, commands={ADDRESS: {"tv": {"power": {"key": "old"}}}},
            learning_task=None, learn_command=AsyncMock(), send_command=AsyncMock())
        self.flow = Flow()
        self.flow.config_entry = SimpleNamespace(runtime_data=self.runtime, options={"retained": 1})
        self.flow.hass = SimpleNamespace(async_create_task=lambda coro, _: asyncio.create_task(coro))
        self.selection = {"address": ADDRESS, "device": "tv", "command": "power"}

    async def asyncTearDown(self):
        tasks = [task for task in (self.flow._task, self.flow._ticker) if task is not None]
        self.flow.async_remove()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def test_existing_key_requires_explicit_replacement_before_start(self):
        result = await self.flow.async_step_init(self.selection)
        self.assertEqual(result["errors"], {"replace": "already_saved"})
        self.runtime.learn_command.assert_not_called()

    async def test_progress_only_prompts_press_after_device_ready(self):
        gate = asyncio.Event()
        async def learn(*args, **kwargs):
            await gate.wait()
        self.runtime.learn_command.side_effect = learn
        result = await self.flow.async_step_init({**self.selection, "replace": True})
        self.assertEqual(result["progress_action"], "connecting")
        await asyncio.sleep(0)
        self.flow._progress({"phase": "ready", "deadline": time.time() + 12})
        result = await self.flow.async_step_learn()
        self.assertEqual(result["progress_action"], "listening")
        self.assertLessEqual(int(result["description_placeholders"]["remaining"]), 12)
        self.flow._progress({"phase": "receiving"})
        self.assertEqual((await self.flow.async_step_learn())["progress_action"], "receiving")
        gate.set()
        await self.flow._task
        self.assertEqual((await self.flow.async_step_learn())["type"], "progress_done")
        self.runtime.send_command.assert_not_called()

    async def test_cancel_closes_the_underlying_session(self):
        gate = asyncio.Event()
        self.runtime.learn_command.side_effect = lambda **_: gate.wait()
        # AsyncMock return handling is explicit here so the task remains in learning.
        async def learn(*args, **_): await gate.wait()
        self.runtime.learn_command.side_effect = learn
        await self.flow.async_step_init({**self.selection, "replace": True})
        await asyncio.sleep(0)
        task = self.flow._task
        self.flow.async_remove()
        with self.assertRaises(asyncio.CancelledError): await task
        self.runtime.send_command.assert_not_called()

    async def test_result_sends_only_on_explicit_test_and_preserves_options(self):
        self.flow._selection = dict(self.selection)
        self.flow._result = {"ok": True, "message": "saved"}
        await self.flow.async_step_result()
        self.runtime.send_command.assert_not_called()
        await self.flow.async_step_result({"action": "test"})
        self.runtime.send_command.assert_awaited_once_with(ADDRESS, "tv", "power")
        result = await self.flow.async_step_result({"action": "finish"})
        self.assertEqual(result["data"], {"retained": 1})

    async def test_wizard_calls_real_runtime_and_saves_a_playable_code(self):
        from test_runtime import GatewayRuntime
        from test_codec import SAMPLES
        runtime = GatewayRuntime(None, SimpleNamespace(data={"host": "127.0.0.1", "port": 8266, "serialno": "ABC123"}, entry_id="flow-test"))
        runtime.devices = {ADDRESS: {}}
        runtime.client = SimpleNamespace(connected=True, capabilities={"learn_v1"},
            learn_ir=AsyncMock(return_value=bytes.fromhex(SAMPLES[0]["raw"])), send_ir=AsyncMock())
        self.flow.config_entry.runtime_data = runtime
        await self.flow.async_step_init(self.selection)
        await self.flow._task
        self.assertTrue(self.flow._task.result()["ok"])
        self.assertEqual(runtime.commands[ADDRESS]["tv"]["power"]["ir_code"], SAMPLES[0]["code"])
        runtime.client.send_ir.assert_not_called()
