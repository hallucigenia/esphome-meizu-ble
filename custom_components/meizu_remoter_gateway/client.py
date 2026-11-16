"""Async TCP protocol, including the fork's correlated IR acknowledgement."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import json
import math
import re
import time
from uuid import uuid4
from typing import Any

LEARN_ERRORS = {
    "ready_timeout": "小圆饼未进入学习状态，请检查电池和距离后重试",
    "learning_timeout": "学习超时，请保持小于 1 厘米对准顶部中心，在提示后短按按键",
    "connect_failed": "无法连接小圆饼，请检查电池并靠近网关",
    "disconnected": "学习时蓝牙连接断开，请靠近网关后重试",
    "cancelled": "学习已取消",
    "learning_busy": "网关正在学习其他按键，请结束后重试",
    "queue_timeout": "网关等待超时，本次学习尚未开始，请重试",
    "device_not_bound": "此小圆饼尚未绑定网关",
    "invalid_header": "收到的学习数据不完整，请重新学习",
    "length_mismatch": "收到的学习数据不完整，请重新学习",
    "repair_failed": "学习数据补收失败，请重新学习",
}


def normalize_address(value: str) -> str:
    value = value.lower()
    # The published 0.3.2 binary omits leading zeroes in address octets.
    if not re.fullmatch(r"(?:[0-9a-f]{1,2}:){5}[0-9a-f]{1,2}", value):
        raise ValueError("Invalid Bluetooth address")
    return ":".join(part.zfill(2) for part in value.split(":"))


def ir_message(address: str, key: str, ir_code: str | None = None, *, max_bytes=511) -> dict:
    data = {"device": normalize_address(address), "key": key}
    for name, value in (("key", key), ("ircode", ir_code)):
        if value is None and name == "ircode":
            continue
        if not isinstance(value, str) or not re.fullmatch(r"(?:[0-9a-fA-F]{2})+", value):
            raise ValueError(f"{name} must contain complete hexadecimal bytes")
        data[name] = value.lower()
    message = {"type": "irsend", "data": data}
    if len(encode(message)) > max_bytes:
        raise ValueError(f"IR command exceeds the transport's {max_bytes}-byte receive limit")
    return message


def encode(message: dict) -> bytes:
    return json.dumps(message, separators=(",", ":"), ensure_ascii=True).encode()


def measurement(status: dict, key: str) -> float | None:
    value = status.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    if key in ("humidity", "battery") and not 0 <= value <= 100:
        return None
    return value


class JsonStream:
    """Parse split or joined JSON objects from an ASCII wire stream."""

    def __init__(self) -> None:
        self.buffer = ""
        self.decoder = json.JSONDecoder()

    def feed(self, chunk: bytes) -> list[dict]:
        self.buffer += chunk.decode("ascii")
        if len(self.buffer) > 65536:
            raise ValueError("Gateway response exceeds the buffer limit")
        result = []
        while self.buffer.strip():
            self.buffer = self.buffer.lstrip()
            try:
                message, end = self.decoder.raw_decode(self.buffer)
            except json.JSONDecodeError:
                break
            self.buffer = self.buffer[end:]
            if not isinstance(message, dict) or not isinstance(message.get("type"), str):
                raise ValueError("Invalid gateway message")
            result.append(message)
        return result


class GatewayClient:
    """One connection with serialized requests, heartbeat, and push callbacks."""

    def __init__(self, host: str, port: int, on_message: Callable[[dict], None]) -> None:
        self.host, self.port, self.on_message = host, port, on_message
        self.reader: asyncio.StreamReader | None = None
        self.writer: asyncio.StreamWriter | None = None
        self.read_task: asyncio.Task | None = None
        self.pending: dict[str, asyncio.Future] = {}
        self.pending_ir: dict[str, tuple[str, asyncio.Future]] = {}
        self.pending_learn: dict[str, tuple[str, asyncio.Future, Callable]] = {}
        self.capabilities: set[str] = set()
        self.ir_lock = asyncio.Lock()
        self.send_lock = asyncio.Lock()
        self.ota_lock = asyncio.Lock()
        self.last_send = 0.0

    @property
    def connected(self) -> bool:
        return self.writer is not None and not self.writer.is_closing() and self.read_task is not None and not self.read_task.done()

    async def connect(self) -> dict:
        self.reader, self.writer = await asyncio.wait_for(asyncio.open_connection(self.host, self.port), 8)
        self.read_task = asyncio.create_task(self._read())
        try:
            message = await self.request("config_info")
            data = message.get("data", {})
            if not isinstance(data.get("serialno"), str) or not re.fullmatch(r"[0-9a-fA-F]{6}", data["serialno"]):
                raise ValueError("Not a Meizu remote gateway")
            capabilities = data.get("capabilities", [])
            self.capabilities = {item for item in capabilities if isinstance(item, str)} if isinstance(capabilities, list) else set()
            return data
        except BaseException:
            await self.close()
            raise

    async def _read(self) -> None:
        stream = JsonStream()
        try:
            while chunk := await self.reader.read(4096):
                for message in stream.feed(chunk):
                    if message["type"] == "irsend_result":
                        self._ir_result(message.get("data"))
                    elif message["type"] == "learn_event":
                        self._learn_event(message.get("data"))
                    waiter = self.pending.get(message["type"])
                    if waiter is not None and not waiter.done():
                        waiter.set_result(message)
                    self.on_message(message)
        finally:
            for waiter in [*self.pending.values(), *(item[1] for item in self.pending_ir.values()),
                           *(item[1] for item in self.pending_learn.values())]:
                if not waiter.done():
                    waiter.set_exception(ConnectionError("Gateway disconnected"))
            if self.writer is not None:
                self.writer.close()

    async def send(self, message: dict) -> None:
        payload = encode(message)
        if len(payload) > 511:
            raise ValueError("Command exceeds the gateway receive limit")
        async with self.send_lock:
            if not self.connected:
                raise ConnectionError("Gateway is not connected")
            # Legacy 0.3.x parses each recv() as one command. Keep a gap for
            # compatibility; the acknowledgement fork also handles stream framing.
            await asyncio.sleep(max(0, .2 - (time.monotonic() - self.last_send)))
            self.writer.write(payload)
            await asyncio.wait_for(self.writer.drain(), 5)
            self.last_send = time.monotonic()

    async def request(self, kind: str, data: dict | None = None, timeout: float = 8) -> dict:
        if kind in self.pending:
            raise RuntimeError(f"Already awaiting {kind}")
        waiter = asyncio.get_running_loop().create_future()
        self.pending[kind] = waiter
        try:
            await self.send({"type": kind, **({"data": data} if data is not None else {})})
            return await asyncio.wait_for(waiter, timeout)
        finally:
            self.pending.pop(kind, None)
            if not waiter.done():
                waiter.cancel()

    @property
    def ir_confirmation(self) -> bool:
        return "ir_ack_v1" in self.capabilities

    async def ota_status(self) -> dict:
        # Idle polling and an install monitor share one read; neither may cancel
        # the other's request or start a second waiter for the same reply type.
        if waiter := self.pending.get("ota_status"):
            return await asyncio.shield(waiter)
        return await self.request("ota_status")

    async def heartbeat(self) -> None:
        # The MCU waits for its BLE worker in the same connection's receive
        # handler. An IR request already counts as network activity, so a short
        # heartbeat timeout must not disconnect a legitimate in-flight command.
        if not self.pending_ir:
            await self.request("heartbeat")

    def _ir_result(self, data: Any) -> None:
        if not isinstance(data, dict) or not isinstance(data.get("request_id"), str):
            return
        pending = self.pending_ir.get(data["request_id"])
        if pending is None or pending[1].done():
            return
        address, waiter = pending
        try:
            if normalize_address(data.get("device", "")) != address:
                raise ValueError("IR response device does not match request")
            if not isinstance(data.get("success"), bool) or not isinstance(data.get("confirmed"), bool):
                raise ValueError("Invalid IR response flags")
            if data["success"] is not data["confirmed"] or not isinstance(data.get("status"), str):
                raise ValueError("Inconsistent IR response")
            if data["confirmed"] and not (
                data["status"] == "ok" and type(data.get("ack")) is int and (
                    data["ack"] == 4 and data.get("cached") is True or
                    data["ack"] == 2 and data.get("cached") is False
                )
            ):
                raise ValueError("IR success is missing a valid Bluetooth acknowledgement")
        except (ValueError, TypeError, AttributeError) as error:
            waiter.set_exception(ValueError(str(error)))
        else:
            waiter.set_result(data)

    async def send_ir(self, message: dict, timeout: float = 60) -> dict:
        """Send once; TCP delivery alone never counts as Bluetooth success."""
        if self.pending_learn:
            raise OSError("网关正在学习红外，请结束学习后再发送")
        async with self.ir_lock:
            if not self.ir_confirmation:
                await self.send(message)
                return {"sent": True, "confirmed": False, "bridge_acknowledged": False, "status": "unconfirmed"}
            request_id = uuid4().hex
            address = normalize_address(message["data"]["device"])
            waiter = asyncio.get_running_loop().create_future()
            self.pending_ir[request_id] = (address, waiter)
            try:
                await self.send({**message, "data": {**message["data"], "request_id": request_id}})
                data = await asyncio.wait_for(waiter, timeout)
                if not data["confirmed"]:
                    raise OSError(f"Meizu IR transmission failed: {data['status']}")
                return {**data, "sent": True, "bridge_acknowledged": True}
            finally:
                self.pending_ir.pop(request_id, None)
                if not waiter.done():
                    waiter.cancel()

    def _learn_event(self, data: Any) -> None:
        if not isinstance(data, dict) or not isinstance(data.get("request_id"), str):
            return
        pending = self.pending_learn.get(data.get("request_id"))
        if pending is None or pending[1].done():
            return
        address, waiter, progress = pending
        try:
            if normalize_address(data.get("device", "")) != address:
                raise ValueError("Learning response device does not match request")
            phase = data.get("phase")
            if phase == "complete":
                raw = data.get("raw")
                if data.get("status") != "ok" or not isinstance(raw, str) or not re.fullmatch(r"(?:[0-9a-fA-F]{2}){10,1500}", raw):
                    raise ValueError("Invalid learning result")
                decoded = bytes.fromhex(raw)
                if type(data.get("length")) is not int or len(decoded) != data["length"]:
                    raise ValueError("Learning result length mismatch")
                waiter.set_result(decoded)
            elif phase in ("failed", "cancelled"):
                status = data.get("status", phase)
                waiter.set_exception(OSError(LEARN_ERRORS.get(status, f"红外学习失败：{status}")))
            elif phase in ("queued", "connecting", "ready", "receiving"):
                if phase == "ready" and (type(data.get("timeout_ms")) is not int or not 1000 <= data["timeout_ms"] <= 15000):
                    raise ValueError("Invalid learning window")
                progress(data)
        except (ValueError, TypeError, AttributeError) as error:
            if not waiter.done():
                waiter.set_exception(ValueError(str(error)))

    async def learn_ir(self, address: str, timeout: int, progress: Callable) -> bytes:
        """One bounded session; cancellation sends stop, never restarts learning."""
        if "learn_v1" not in self.capabilities:
            raise ValueError("网关固件不支持原生学码，请升级至 0.6.0")
        if self.pending_learn or self.ir_lock.locked():
            raise OSError("网关正在执行其他红外操作，请稍后重试")
        if type(timeout) is not int or not 1 <= timeout <= 15:
            raise ValueError("学习窗口必须为 1–15 秒")
        address = normalize_address(address)
        request_id = uuid4().hex
        waiter = asyncio.get_running_loop().create_future()
        self.pending_learn[request_id] = (address, waiter, progress)
        completed = False
        try:
            await self.send({"type": "learn_start", "data": {
                "request_id": request_id, "device": address, "timeout": timeout}})
            raw = await asyncio.wait_for(waiter, 70)
            completed = True
            return raw
        finally:
            if not completed and self.connected:
                try:
                    await asyncio.wait_for(self.send({"type": "learn_cancel", "data": {
                        "request_id": request_id, "device": address}}), 3)
                except (OSError, TimeoutError):
                    pass
            self.pending_learn.pop(request_id, None)
            if not waiter.done():
                waiter.cancel()

    async def close(self) -> None:
        if self.writer is not None:
            self.writer.close()
            try:
                await self.writer.wait_closed()
            except OSError:
                pass
        if self.read_task is not None:
            self.read_task.cancel()
            await asyncio.gather(self.read_task, return_exceptions=True)
        self.writer = None
        self.reader = None
        self.read_task = None
