"""Native ESPHome transport: origin checks, real acknowledgements and cancellation."""
import asyncio
import importlib.util
import inspect
import json
from pathlib import Path
import threading
from types import ModuleType, SimpleNamespace
import sys
import unittest
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[2] / "custom_components/meizu_remoter_gateway"
PACKAGE = "meizu_esphome_test_package"
package = ModuleType(PACKAGE)
package.__path__ = [str(ROOT)]
sys.modules[PACKAGE] = package
SPEC = importlib.util.spec_from_file_location(PACKAGE + ".esphome_client", ROOT / "esphome_client.py")
module = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = module
# Keep these protocol tests dependency-free, but preserve HA's callback marker.
core = ModuleType("homeassistant.core")
def callback(func):
    func._hass_callback = True
    return func
core.callback = callback
with patch.dict(sys.modules, {"homeassistant": ModuleType("homeassistant"), "homeassistant.core": core}):
    SPEC.loader.exec_module(module)
ADDRESS = "02:00:00:00:00:01"
SETTINGS = {"esphome_device_id": "synthetic-node", "esphome_service": "test_node_meizu_puck", "api_source": "meizu_puck",
            "address": ADDRESS, "serialno": "ABC123"}
SAMPLE = json.loads((Path(__file__).parent / "fixtures/learned.json").read_text())[0]


class Bus:
    def __init__(self): self.listeners, self.jobs = [], []
    def async_listen(self, event_type, callback):
        assert event_type == "esphome.meizu_ble"
        self.listeners.append(callback)
        return lambda: self.listeners.remove(callback)
    def fire(self, values):
        loop = asyncio.get_running_loop()
        for listener in list(self.listeners):
            event = SimpleNamespace(data=values)
            if getattr(listener, "_hass_callback", False):
                listener(event)
            elif inspect.iscoroutinefunction(listener):
                self.jobs.append(loop.create_task(listener(event)))
            else:
                # Like HassJob: an unmarked sync listener runs in the executor.
                self.jobs.append(loop.run_in_executor(None, listener, event))

    async def drain(self):
        jobs, self.jobs = self.jobs, []
        if jobs:
            await asyncio.gather(*jobs)


class Services:
    def __init__(self, bus): self.bus, self.calls, self.auto_status, self.writes = bus, [], True, asyncio.Queue()
    def has_service(self, domain, service): return domain == "esphome" and service == SETTINGS["esphome_service"]
    async def async_call(self, domain, service, args, blocking):
        assert self.has_service(domain, service) and blocking
        self.calls.append(dict(args))
        self.writes.put_nowait(dict(args))
        if args["operation"] == "status" and self.auto_status:
            self.bus.fire(event(args["request_id"], operation="status"))


def event(request_id, **overrides):
    return {"protocol": "meizu_ble_v1", "device_id": SETTINGS["esphome_device_id"], "source": SETTINGS["api_source"], "address": ADDRESS,
            "request_id": request_id, "operation": "send", "phase": "complete", "status": "ok",
            "confirmed": "1", "cached": "1", "ack": "4", "available": "1", "measurement_age_ms": "360000",
            "update_interval": "5", "version": "0.1.0-esphome", "temperature": "20", "humidity": "50",
            "voltage": "3", "battery": "100", **overrides}


class EventDispatchTests(unittest.IsolatedAsyncioTestCase):
    async def test_control_path_stays_disconnected_until_status_handshake_completes(self):
        bus = Bus()
        services = Services(bus)
        services.auto_status = False
        client = module.ESPHomeGatewayClient(SimpleNamespace(bus=bus, services=services), SETTINGS, lambda message: None)
        task = asyncio.create_task(client.connect())
        try:
            call = await asyncio.wait_for(services.writes.get(), 1)
            self.assertFalse(client.connected, "Service registration is not a successful device handshake")
            with self.assertRaises(OSError):
                await client.request("devices")
            bus.fire(event(call["request_id"], operation="status"))
            await task
            self.assertTrue(client.connected)
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            await client.close()

    async def test_unmarked_listener_runs_in_executor_and_marked_listener_stays_in_loop(self):
        bus, threads = Bus(), {}
        loop_thread = threading.get_ident()
        bus.async_listen("esphome.meizu_ble", lambda event: threads.update(executor=threading.get_ident()))
        @callback
        def in_loop(event):
            threads["callback"] = threading.get_ident()
        bus.async_listen("esphome.meizu_ble", in_loop)
        bus.fire({})
        await bus.drain()
        self.assertNotEqual(threads["executor"], loop_thread)
        self.assertEqual(threads["callback"], loop_thread)

    async def test_status_result_completes_future_in_event_loop(self):
        loop = asyncio.get_running_loop()
        loop.set_debug(True)
        bus = Bus()
        client = module.ESPHomeGatewayClient(SimpleNamespace(bus=bus), SETTINGS, lambda message: None)
        waiter = loop.create_future()
        # A scheduled done callback makes asyncio debug detect a foreign-thread write.
        waiter.add_done_callback(lambda future: None)
        client.pending["config_info"] = waiter
        client._status_requests["status-id"] = "config_info"
        bus.async_listen("esphome.meizu_ble", client._event)
        bus.fire(event("status-id", operation="status"))
        await bus.drain()
        self.assertEqual(waiter.result()["data"]["serialno"], SETTINGS["serialno"])


class ESPHomeTransportTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.bus = Bus()
        self.services = Services(self.bus)
        self.messages = []
        self.client = module.ESPHomeGatewayClient(SimpleNamespace(bus=self.bus, services=self.services), SETTINGS, self.messages.append)
        info = await self.client.connect()
        self.assertEqual(info["serialno"], "ABC123")
        self.assertEqual(self.messages, [])  # Gateway registry is created before the first device report.
        self.services.writes.get_nowait()

    async def asyncTearDown(self):
        await self.client.close()
        await self.bus.drain()

    async def begin_send(self, code=""):
        packet = {"type": "irsend", "data": {"device": ADDRESS, "key": "1e001c000000000000"}}
        if code: packet["data"]["ircode"] = code
        task = asyncio.create_task(self.client.send_ir(packet))
        call = await asyncio.wait_for(self.services.writes.get(), 1)
        return task, call["request_id"]

    async def test_status_roundtrip_retains_measurement_age(self):
        await self.client.request("devices")
        data = self.messages[-1]["data"]["devices"][0]
        self.assertEqual(data["measurement_age_ms"], 360000)
        self.assertEqual(data["status"]["temperature"], 20)
        self.assertTrue(self.client.ir_confirmation)

    async def test_service_acceptance_and_wrong_origins_never_confirm_an_ir_send(self):
        task, request_id = await self.begin_send()
        self.assertFalse(task.done())
        for change in ({"device_id": "other-node"}, {"source": "meizu_other"}, {"address": "02:00:00:00:00:02"}, {"request_id": "stale"}):
            self.bus.fire(event(request_id) | change)
            await asyncio.sleep(0)
            self.assertFalse(task.done())
        self.bus.fire(event(request_id))
        result = await task
        self.assertTrue(result["confirmed"] and result["bridge_acknowledged"])
        self.assertEqual(result["ack"], 4)
        self.assertEqual(len([call for call in self.services.calls if call["operation"] == "send"]), 1)

    async def test_inconsistent_bluetooth_ack_and_failure_are_not_retried(self):
        task, request_id = await self.begin_send()
        self.bus.fire(event(request_id, cached="0"))
        with self.assertRaises(ValueError): await task
        task, request_id = await self.begin_send()
        self.bus.fire(event(request_id, phase="failed", status="cache_miss", confirmed="0", cached="0", ack="0"))
        with self.assertRaises(OSError): await task
        self.assertEqual(len([call for call in self.services.calls if call["operation"] == "send"]), 2)

    async def test_concurrent_send_is_rejected_instead_of_transmitting_after_the_first_request(self):
        task, request_id = await self.begin_send()
        packet = {"type": "irsend", "data": {"device": ADDRESS, "key": "1e001c000000000000"}}
        with self.assertRaisesRegex(OSError, "not queued"):
            await self.client.send_ir(packet)
        self.assertEqual(len([call for call in self.services.calls if call["operation"] == "send"]), 1)
        self.bus.fire(event(request_id))
        await task

    async def test_learning_ready_capture_conversion_and_no_transmission(self):
        progress = []
        context = "客厅电视📺"
        task = asyncio.create_task(self.client.learn_ir(ADDRESS, 15, progress.append, device=context))
        request_id = (await asyncio.wait_for(self.services.writes.get(), 1))["request_id"]
        self.assertEqual(self.services.calls[-1]["device"], context)
        self.assertEqual(progress, [])
        self.bus.fire(event(request_id, operation="learn", phase="ready", timeout_ms="15000"))
        converted = module.convert_capture(bytes.fromhex(SAMPLE["raw"]), context)
        self.bus.fire(event(request_id, operation="learn", raw=SAMPLE["raw"], ir_code=converted["ir_code"],
                            key=converted["key"], length=str(len(bytes.fromhex(SAMPLE["raw"])))))
        self.assertEqual(await task, bytes.fromhex(SAMPLE["raw"]))
        self.assertEqual(progress[0]["phase"], "ready")
        self.assertFalse(any(call["operation"] == "send" for call in self.services.calls))

    async def test_learning_converter_mismatch_preserves_failure_and_sends_cancel(self):
        task = asyncio.create_task(self.client.learn_ir(ADDRESS, 15, lambda state: None))
        request_id = (await asyncio.wait_for(self.services.writes.get(), 1))["request_id"]
        self.bus.fire(event(request_id, operation="learn", raw=SAMPLE["raw"], ir_code="00", key="00", length="40"))
        with self.assertRaises(ValueError): await task
        self.assertEqual(self.services.calls[-1]["operation"], "cancel")
        self.assertEqual(self.services.calls[-1]["request_id"], request_id)

    async def test_cancelled_task_stops_the_same_learning_transaction_once(self):
        task = asyncio.create_task(self.client.learn_ir(ADDRESS, 15, lambda state: None))
        request_id = (await asyncio.wait_for(self.services.writes.get(), 1))["request_id"]
        task.cancel()
        with self.assertRaises(asyncio.CancelledError): await task
        self.assertEqual([call["operation"] for call in self.services.calls], ["status", "learn", "cancel"])
        self.assertEqual(self.services.calls[-1]["request_id"], request_id)
        self.assertFalse(self.client.pending_learn)

    async def test_close_fails_pending_request_and_removes_listener(self):
        task, _ = await self.begin_send()
        await self.client.close()
        with self.assertRaises(OSError): await task
        self.assertFalse(self.bus.listeners)

    async def test_invalid_status_is_rejected_before_it_can_update_devices(self):
        self.services.auto_status = False
        task = asyncio.create_task(self.client.request("devices", timeout=.1))
        request_id = (await asyncio.wait_for(self.services.writes.get(), 1))["request_id"]
        self.bus.fire(event(request_id, operation="status", update_interval="0"))
        with self.assertRaises(ValueError): await task
        self.assertEqual(self.messages, [])
        self.assertFalse(self.client._status_requests)

    async def test_yaml_managed_settings_fail_without_device_io(self):
        before = len(self.services.calls)
        with self.assertRaises(ValueError): await self.client.request("bind")
        with self.assertRaises(ValueError): await self.client.request("setinterval", {"update_interval": 5})
        self.assertEqual(len(self.services.calls), before)

    async def test_native_capture_reaches_existing_runtime_store_and_disk_failure_preserves_the_old_key(self):
        from test_runtime import GatewayRuntime
        runtime = GatewayRuntime(None, SimpleNamespace(data={"host": "unused.invalid", "port": 8266, "serialno": "ABC123"}, entry_id="native-test"))
        runtime.transport = "esphome"
        runtime.client = self.client
        self.client.on_message = runtime.message
        await self.client.request("devices")
        self.services.writes.get_nowait()
        runtime.commands = {ADDRESS: {"tv": {"power": {"key": "old"}}}}
        runtime.store = SimpleNamespace(async_save=AsyncMock(side_effect=OSError("disk full")))
        task = asyncio.create_task(runtime.learn_command(ADDRESS, "tv", "power"))
        call = await asyncio.wait_for(self.services.writes.get(), 1)
        self.assertEqual(call["device"], "tv")
        converted = module.convert_capture(bytes.fromhex(SAMPLE["raw"]), "tv")
        self.bus.fire(event(call["request_id"], operation="learn", raw=SAMPLE["raw"], key=converted["key"],
                            ir_code=converted["ir_code"], length=str(len(bytes.fromhex(SAMPLE["raw"])))))
        with self.assertRaisesRegex(RuntimeError, "disk full"):
            await task
        self.assertEqual(runtime.commands[ADDRESS]["tv"]["power"]["key"], "old")
        self.assertIsNone(runtime.learning_task)
        runtime.store.async_save.assert_awaited_once()

    async def test_failed_poll_immediately_marks_the_puck_unavailable(self):
        self.bus.fire(event("", operation="poll", phase="failed", status="connect_failed", available="0", measurement_age_ms="-1"))
        self.assertEqual(self.messages[-1]["data"]["devices"][0]["available"], 0)


if __name__ == "__main__":
    unittest.main()
