"""Entity snapshots must not mutate previously published HA state."""
import ast
import asyncio
from copy import deepcopy
from enum import IntFlag
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

FILE = Path(__file__).resolve().parents[2] / "custom_components/meizu_remoter_gateway/remote.py"
TREE = ast.parse(FILE.read_text())
REMOTE = next(node for node in TREE.body if isinstance(node, ast.ClassDef) and node.name == "MeizuRemote")


class Feature(IntFlag):
    LEARN_COMMAND = 1
    DELETE_COMMAND = 2


class Base:
    def __init__(self, runtime, address, suffix): self.runtime, self.address = runtime, address


NS = {"MeizuEntity": Base, "RemoteEntity": type("RemoteEntity", (), {}),
      "RemoteEntityFeature": Feature, "asyncio": asyncio, "deepcopy": deepcopy,
      "ServiceValidationError": ValueError}
exec(compile(ast.Module(body=[REMOTE], type_ignores=[]), str(FILE), "exec"), NS)


class RemoteTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.address = "02:00:00:00:ab:01"
        self.runtime = SimpleNamespace(serial="ABC123", commands={}, last_ir_results={},
            client=SimpleNamespace(capabilities={"learn_v1"}, ir_confirmation=True),
            learning={"address": self.address, "phase": "connecting"},
            command_lock=asyncio.Lock(), store=SimpleNamespace(async_save=AsyncMock()), notify=lambda: None)
        self.remote = NS["MeizuRemote"](self.runtime, self.address)

    async def test_learning_state_does_not_change_a_previously_published_snapshot(self):
        before = self.remote.extra_state_attributes
        self.runtime.learning["phase"] = "ready"
        after = self.remote.extra_state_attributes
        self.assertEqual(before["learning"]["phase"], "connecting")
        self.assertEqual(after["learning"]["phase"], "ready")
        self.assertNotEqual(before, after)

    async def test_legacy_firmware_does_not_advertise_learning(self):
        self.assertTrue(self.remote.supported_features & Feature.LEARN_COMMAND)
        self.runtime.client.capabilities.clear()
        self.assertFalse(self.remote.supported_features & Feature.LEARN_COMMAND)

    async def test_delete_disk_failure_retains_the_in_memory_command(self):
        self.runtime.commands = {self.address: {"tv": {"power": {"key": "aa"}}}}
        self.runtime.store.async_save.side_effect = OSError("disk full")
        with self.assertRaises(OSError):
            await self.remote.async_delete_command(device="tv", command=["power"])
        self.assertIn("power", self.runtime.commands[self.address]["tv"])
