"""Exercise the wire protocol against a local MCU-like TCP server."""

import asyncio
import importlib.util
import json
from pathlib import Path
import unittest

FILE = Path(__file__).resolve().parents[2] / "custom_components/meizu_remoter_gateway/client.py"
SPEC = importlib.util.spec_from_file_location("meizu_client_test", FILE)
client = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(client)
ADDRESS = "02:00:00:00:AB:02"


class StreamTests(unittest.TestCase):
    def test_old_binary_unpadded_bluetooth_address_is_canonicalized(self):
        self.assertEqual(client.normalize_address("02:00:00:00:ab:0"), "02:00:00:00:ab:00")
        self.assertEqual(client.normalize_address("1:2:3:4:5:6"), "01:02:03:04:05:06")

    def test_split_and_joined_messages(self):
        stream = client.JsonStream()
        self.assertEqual(stream.feed(b'{"type":"up'), [])
        self.assertEqual(stream.feed(b'date","data":{"a":1}} {"type":"heartbeat"}'), [
            {"type": "update", "data": {"a": 1}}, {"type": "heartbeat"}])

    def test_unbounded_or_nonobject_messages_rejected(self):
        with self.assertRaises(ValueError):
            client.JsonStream().feed(b"x" * 65537)
        with self.assertRaises(ValueError):
            client.JsonStream().feed(b"[]")

    def test_ir_requires_valid_mac_and_complete_bytes(self):
        for address, key, code in (("bad", "ab", None), (ADDRESS, "abc", None), (ADDRESS, "zz", None), (ADDRESS, "ab", "")):
            with self.subTest(address=address, key=key, code=code), self.assertRaises(ValueError):
                client.ir_message(address, key, code)
        self.assertEqual(client.ir_message(ADDRESS, "AABB", "0011"), {
            "type": "irsend", "data": {"device": ADDRESS.lower(), "key": "aabb", "ircode": "0011"}})

    def test_firmware_buffer_limit_is_enforced_before_transmission(self):
        with self.assertRaisesRegex(ValueError, "511"):
            client.ir_message(ADDRESS, "aa" * 9, "bb" * 250)

    def test_missing_invalid_values_never_become_zero(self):
        for value in (None, True, "12", float("nan"), float("inf"), -1, 101):
            with self.subTest(value=value):
                self.assertIsNone(client.measurement({"humidity": value}, "humidity"))
        self.assertEqual(client.measurement({"humidity": 0}, "humidity"), 0)
        self.assertEqual(client.measurement({"temperature": -8.5}, "temperature"), -8.5)


class ConnectionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.messages = []
        self.connections = set()
        self.stall = False
        self.ack_mode = "legacy"
        self.ir_requests = []
        self.learn_requests = []
        self.learn_mode = "complete"
        self.server = await asyncio.start_server(self.serve, "127.0.0.1", 0)
        self.gateway = client.GatewayClient("127.0.0.1", self.server.sockets[0].getsockname()[1], self.messages.append)

    async def asyncTearDown(self):
        await self.gateway.close()
        for writer in self.connections.copy():
            writer.close()
            await writer.wait_closed()
        self.server.close()
        await self.server.wait_closed()
        await asyncio.sleep(0)

    async def serve(self, reader, writer):
        self.connections.add(writer)
        stream = client.JsonStream()
        try:
            while chunk := await reader.read(4096):
                for message in stream.feed(chunk):
                    kind = message["type"]
                    if kind == "config_info":
                        # Intentionally split a reply across packets.
                        writer.write(b'{"type":"config_info","data":')
                        await writer.drain()
                        await asyncio.sleep(.01)
                        writer.write(b'{"version":"0.3.2","serialno":"ABC123","update_interval":5}}')
                    elif kind == "devices":
                        writer.write(b'{"type":"devices","data":{"devices":[]}}{"type":"update","data":{"device":"02:00:00:00:ab:02","available":0}}')
                    elif kind == "heartbeat" and not self.stall:
                        writer.write(client.encode(message))
                    elif kind == "ota_status":
                        await asyncio.sleep(.05)
                        writer.write(client.encode({"type": "ota_status", "data": {"phase": "verifying_boot"}}))
                    elif kind == "disconnect":
                        writer.close()
                    elif kind == "learn_start":
                        self.learn_requests.append(message)
                        data = message["data"]
                        base = {"request_id": data["request_id"], "device": data["device"]}
                        writer.write(client.encode({"type": "learn_event", "data": {**base, "phase": "ready", "timeout_ms": 15000}}))
                        if self.learn_mode == "complete":
                            result = {**base, "phase": "complete", "status": "ok", "length": 40, "raw": "ab" * 40}
                            writer.write(client.encode({"type": "learn_event", "data": {**result, "request_id": "old-session"}}))
                            writer.write(client.encode({"type": "learn_event", "data": result}))
                        elif self.learn_mode == "bad_length":
                            writer.write(client.encode({"type": "learn_event", "data": {**base, "phase": "complete", "status": "ok", "length": 41, "raw": "ab" * 40}}))
                        elif self.learn_mode == "disconnect":
                            writer.close()
                    elif kind == "learn_cancel":
                        self.learn_requests.append(message)
                    elif kind == "irsend":
                        self.ir_requests.append(message)
                        if self.ack_mode not in ("legacy", "timeout"):
                            data = message["data"]
                            result = {"request_id": data["request_id"], "device": data["device"],
                                      "success": True, "confirmed": True, "status": "ok", "ack": 4, "cached": True}
                            if self.ack_mode == "miss":
                                result.update(success=False, confirmed=False, status="cache_miss", ack=0, cached=False)
                            elif self.ack_mode == "wrong_device":
                                result["device"] = "00:11:22:33:44:55"
                            elif self.ack_mode == "fake_success":
                                result["ack"] = 0
                            # An unrelated reply must not release the waiter.
                            writer.write(client.encode({"type": "irsend_result", "data": {**result, "request_id": "other"}}))
                            writer.write(client.encode({"type": "irsend_result", "data": result}))
                    await writer.drain()
        except (ConnectionError, OSError):
            pass
        finally:
            writer.close()
            await writer.wait_closed()
            self.connections.discard(writer)

    async def test_identification_and_push_survive_split_joined_responses(self):
        info = await self.gateway.connect()
        self.assertEqual(info["serialno"], "ABC123")
        self.assertTrue(self.gateway.connected)
        await self.gateway.send({"type": "subscribe"})
        reply = await self.gateway.request("devices")
        self.assertEqual(reply["data"]["devices"], [])
        await asyncio.sleep(.01)
        self.assertTrue(any(m["type"] == "update" for m in self.messages))

    async def test_heartbeat_and_clean_close(self):
        await self.gateway.connect()
        self.assertEqual(await self.gateway.request("heartbeat"), {"type": "heartbeat"})
        await self.gateway.close()
        self.assertFalse(self.gateway.connected)
        self.assertEqual(self.gateway.pending, {})

    async def test_learning_correlates_results_and_reports_device_readiness(self):
        await self.gateway.connect()
        self.gateway.capabilities.add("learn_v1")
        progress = []
        self.assertEqual(await self.gateway.learn_ir(ADDRESS, 15, progress.append), b"\xab" * 40)
        self.assertEqual(progress[0]["phase"], "ready")
        self.assertEqual(self.gateway.pending_learn, {})
        self.assertEqual([r["type"] for r in self.learn_requests], ["learn_start"])

    async def test_heartbeat_remains_responsive_and_cancel_sends_stop(self):
        await self.gateway.connect()
        self.gateway.capabilities.add("learn_v1")
        self.learn_mode = "wait"
        ready = asyncio.Event()
        task = asyncio.create_task(self.gateway.learn_ir(ADDRESS, 15, lambda _: ready.set()))
        await asyncio.wait_for(ready.wait(), 2)
        await asyncio.wait_for(self.gateway.heartbeat(), 2)
        with self.assertRaisesRegex(OSError, "正在学习"):
            await self.gateway.send_ir(client.ir_message(ADDRESS, "aa" * 9))
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        await asyncio.sleep(.02)
        self.assertEqual([r["type"] for r in self.learn_requests], ["learn_start", "learn_cancel"])
        self.assertEqual(self.learn_requests[0]["data"]["request_id"], self.learn_requests[1]["data"]["request_id"])
        self.assertEqual(self.gateway.pending_learn, {})

    async def test_learning_bad_result_and_connection_loss_never_report_success(self):
        for mode, expected in (("bad_length", ValueError), ("disconnect", ConnectionError)):
            with self.subTest(mode=mode):
                if not self.gateway.connected:
                    await self.gateway.connect()
                self.gateway.capabilities.add("learn_v1")
                self.learn_mode = mode
                with self.assertRaises(expected):
                    await self.gateway.learn_ir(ADDRESS, 15, lambda _: None)
                self.assertEqual(self.gateway.pending_learn, {})

    async def test_timeout_clears_pending_request(self):
        await self.gateway.connect()
        self.stall = True
        with self.assertRaises(TimeoutError):
            await self.gateway.request("heartbeat", timeout=.3)
        self.assertEqual(self.gateway.pending, {})

    async def test_disconnect_fails_waiter_without_waiting_for_timeout(self):
        await self.gateway.connect()
        with self.assertRaises(ConnectionError):
            await self.gateway.request("disconnect", timeout=2)
        self.assertFalse(self.gateway.connected)

    async def test_ir_send_does_not_wait_for_nonexistent_acknowledgement(self):
        await self.gateway.connect()
        await self.gateway.send(client.ir_message(ADDRESS, "aabb", "0011"))
        self.assertEqual(self.gateway.pending, {})

    async def test_legacy_firmware_never_claims_confirmation(self):
        await self.gateway.connect()
        result = await self.gateway.send_ir(client.ir_message(ADDRESS, "aabb"))
        self.assertFalse(result["confirmed"])
        self.assertFalse(result["bridge_acknowledged"])

    async def test_correlated_bluetooth_ack_confirms_send(self):
        await self.gateway.connect()
        self.gateway.capabilities = {"ir_ack_v1"}
        self.ack_mode = "cached"
        result = await self.gateway.send_ir(client.ir_message(ADDRESS, "aa" * 9))
        self.assertTrue(result["confirmed"])
        self.assertEqual(result["ack"], 4)
        self.assertTrue(result["bridge_acknowledged"])
        self.assertEqual(self.gateway.pending_ir, {})

    async def test_failure_mismatch_and_timeout_do_not_retry(self):
        await self.gateway.connect()
        self.gateway.capabilities = {"ir_ack_v1"}
        for mode, error in (("miss", OSError), ("wrong_device", ValueError), ("fake_success", ValueError), ("timeout", TimeoutError)):
            with self.subTest(mode=mode):
                self.ack_mode = mode
                count = len(self.ir_requests)
                with self.assertRaises(error):
                    await self.gateway.send_ir(client.ir_message(ADDRESS, "aa" * 9), timeout=.4)
                self.assertEqual(len(self.ir_requests), count + 1)
                self.assertEqual(self.gateway.pending_ir, {})

    async def test_disconnect_fails_ir_waiter(self):
        await self.gateway.connect()
        self.gateway.capabilities = {"ir_ack_v1"}
        self.ack_mode = "timeout"
        sending = asyncio.create_task(self.gateway.send_ir(client.ir_message(ADDRESS, "aa" * 9)))
        await asyncio.sleep(.25)
        for writer in self.connections:
            writer.close()
        with self.assertRaises(ConnectionError):
            await sending
        self.assertEqual(self.gateway.pending_ir, {})

    async def test_heartbeat_cannot_interrupt_an_inflight_ir_request(self):
        await self.gateway.connect()
        waiter = asyncio.get_running_loop().create_future()
        self.gateway.pending_ir["active"] = (ADDRESS, waiter)
        sent = self.gateway.last_send
        await self.gateway.heartbeat()
        self.assertEqual(self.gateway.last_send, sent)
        self.assertNotIn("heartbeat", self.gateway.pending)
        self.gateway.pending_ir.clear()
        waiter.cancel()
        await self.gateway.heartbeat()
        self.assertTrue(any(message["type"] == "heartbeat" for message in self.messages))

    async def test_status_pollers_share_one_request_and_cancellation_is_isolated(self):
        await self.gateway.connect()
        owner = asyncio.create_task(self.gateway.ota_status())
        await asyncio.sleep(.01)
        observer = asyncio.create_task(self.gateway.ota_status())
        await asyncio.sleep(.01)
        observer.cancel()
        with self.assertRaises(asyncio.CancelledError): await observer
        result = await owner
        self.assertEqual(result["data"]["phase"], "verifying_boot")
        self.assertEqual(len([message for message in self.messages if message["type"] == "ota_status"]), 1)
        self.assertNotIn("ota_status", self.gateway.pending)


if __name__ == "__main__":
    unittest.main()
