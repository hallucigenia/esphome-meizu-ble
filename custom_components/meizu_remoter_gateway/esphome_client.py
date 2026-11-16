"""ESPHome service/event transport; a service write is never an IR acknowledgement."""
import asyncio
import math
import re
from uuid import uuid4

from homeassistant.core import callback

from .client import GatewayClient, normalize_address
from .codec import convert_capture


class ESPHomeGatewayClient(GatewayClient):
    max_message_bytes = 16384

    def __init__(self, hass, settings, on_message):
        super().__init__("", 0, on_message)
        self.hass = hass
        self.service = settings["esphome_service"]
        self.source = settings["api_source"]
        self.device_id = settings["esphome_device_id"]
        self.address = normalize_address(settings["address"])
        self.serial = settings["serialno"]
        if not re.fullmatch(r"[a-z0-9_]+", self.service) or not re.fullmatch(r"meizu_[a-z0-9_]+", self.source):
            raise ValueError("Invalid ESPHome service name")
        if not self.service.endswith("_" + self.source):
            raise ValueError("ESPHome service must end with the component's api_service name")
        self._connected = False
        self._connecting = False
        self._unsubscribe = None
        self._status_requests = {}
        self._learn_context = "remote"
        self.capabilities = {"ir_ack_v1", "learn_v1", "measurement_age_v1", "esphome_v1"}

    @property
    def connected(self):
        return self._connected and self.hass.services.has_service("esphome", self.service)

    async def connect(self):
        if not self.hass.services.has_service("esphome", self.service):
            raise OSError("ESPHome action is unavailable; add the ESPHome device first")
        if self._unsubscribe is None:
            self._unsubscribe = self.hass.bus.async_listen("esphome.meizu_ble", self._event)
        self._connected = False
        self._connecting = True
        try:
            info = (await self.request("config_info"))["data"]
            self._connected = True
            return info
        except BaseException:
            await self.close()
            raise
        finally:
            self._connecting = False

    async def request(self, message_type, data=None, timeout=6):
        try:
            return await super().request(message_type, data, timeout)
        finally:
            for request_id, kind in list(self._status_requests.items()):
                if kind == message_type:
                    self._status_requests.pop(request_id, None)

    async def send(self, message):
        kind, data = message["type"], message.get("data", {})
        handshake = self._connecting and kind == "config_info" and self.hass.services.has_service("esphome", self.service)
        if not self.connected and not handshake:
            raise OSError("ESPHome transport is disconnected")
        if kind == "subscribe":
            return
        args = {"request_id": data.get("request_id", ""), "operation": "", "key": "", "ir_code": "", "timeout": 15, "device": "remote"}
        if kind in {"config_info", "devices", "heartbeat"}:
            args.update(request_id=uuid4().hex, operation="status")
            self._status_requests[args["request_id"]] = kind
        elif kind in {"irsend", "learn_start", "learn_cancel"}:
            if normalize_address(data["device"]) != self.address:
                raise ValueError("ESPHome action belongs to a different puck")
            args["operation"] = {"irsend": "send", "learn_start": "learn", "learn_cancel": "cancel"}[kind]
            if kind == "irsend":
                args.update(key=data["key"], ir_code=data.get("ircode", ""))
            elif kind == "learn_start":
                args.update(timeout=data["timeout"], device=self._learn_context)
        else:
            raise ValueError("This setting is managed in ESPHome YAML, not by the gateway TCP service")
        try:
            await asyncio.wait_for(self.hass.services.async_call("esphome", self.service, args, blocking=True), 5)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            raise OSError("Could not call the configured ESPHome action") from error

    async def learn_ir(self, address, timeout, progress, *, device="remote"):
        if self.pending_learn:
            raise OSError("Another learning session is active")
        self._learn_context = device
        try:
            return await super().learn_ir(address, timeout, progress)
        finally:
            self._learn_context = "remote"

    async def send_ir(self, message, timeout=70):
        if self.ir_lock.locked() or self.pending_learn:
            raise OSError("ESPHome controller is busy; the request was not queued")
        # The MCU bounds starting a request to 15s and its active send to 45s.
        # Leave time for both, rather than timing out HA while the MCU is still working.
        return await super().send_ir(message, timeout)

    @staticmethod
    def _integer(data, key, minimum, maximum):
        value = data.get(key)
        if not isinstance(value, str) or re.fullmatch(r"-?\d{1,10}", value) is None:
            raise ValueError("Invalid ESPHome integer")
        number = int(value)
        if not minimum <= number <= maximum:
            raise ValueError("ESPHome integer is out of range")
        return number

    @staticmethod
    def _flag(data, key):
        if data.get(key) not in {"0", "1"}:
            raise ValueError("Invalid ESPHome result flag")
        return data[key] == "1"

    def _device_report(self, data):
        status = {"manufacturer": "Meizu", "model": "R16"}
        available = self._flag(data, "available")
        age = self._integer(data, "measurement_age_ms", -1, 4294967295)
        if available:
            if age < 0:
                raise ValueError("Available measurement has no age")
            for key in ("temperature", "humidity", "voltage", "battery", "rssi"):
                if key in data:
                    if not isinstance(data[key], str):
                        raise ValueError("Invalid ESPHome measurement type")
                    value = float(data[key])
                    if not math.isfinite(value):
                        raise ValueError("Invalid ESPHome measurement")
                    status[key] = value
            if not {"temperature", "humidity", "voltage", "battery"} <= status.keys():
                raise ValueError("Incomplete ESPHome measurement")
        return {"device": self.address, "available": int(available), "measurement_age_ms": age, "status": status}

    @callback
    def _event(self, event):
        data = event.data
        if not isinstance(data, dict) or data.get("protocol") != "meizu_ble_v1" or data.get("device_id") != self.device_id or data.get("source") != self.source:
            return
        try:
            if normalize_address(data.get("address", "")) != self.address:
                return
        except (ValueError, AttributeError):
            return
        request_id = data.get("request_id")
        if not isinstance(request_id, str):
            return
        try:
            operation, phase = data.get("operation"), data.get("phase")
            if operation == "poll" and phase in {"complete", "failed"}:
                # Preserve the device-reported sample age, including after an HA reconnect.
                report = self._device_report(data)
                if phase == "complete" and data.get("status") != "ok" or phase == "failed" and report["available"]:
                    raise ValueError("Inconsistent ESPHome poll result")
                self.on_message({"type": "devices", "data": {"devices": [report]}})
            elif operation == "status" and request_id in self._status_requests:
                kind = self._status_requests[request_id]
                interval = self._integer(data, "update_interval", 1, 30)
                if data.get("status") != "ok" or phase != "complete":
                    raise ValueError("ESPHome status request failed")
                report = self._device_report(data)
                if kind != "config_info":
                    self.on_message({"type": "devices", "data": {"devices": [report]}})
                info = {"serialno": self.serial, "version": data.get("version", "esphome"), "update_interval": interval}
                payload = info if kind == "config_info" else {"devices": [report]} if kind == "devices" else {}
                waiter = self.pending.get(kind)
                if waiter is not None and not waiter.done():
                    waiter.set_result({"type": kind, "data": payload})
                self._status_requests.pop(request_id, None)
            elif operation == "send" and request_id in self.pending_ir:
                result = {"request_id": request_id, "device": self.address, "status": data.get("status"),
                          "success": phase == "complete", "confirmed": self._flag(data, "confirmed"),
                          "cached": self._flag(data, "cached"), "ack": self._integer(data, "ack", 0, 255)}
                self._ir_result(result)
            elif operation == "learn" and request_id in self.pending_learn:
                result = {"request_id": request_id, "device": self.address, "phase": phase, "status": data.get("status")}
                if phase == "ready":
                    result["timeout_ms"] = self._integer(data, "timeout_ms", 1000, 15000)
                elif phase == "receiving":
                    result.update(received=self._integer(data, "received", 0, 1500), length=self._integer(data, "length", 0, 1500))
                elif phase == "complete":
                    raw = data.get("raw", "")
                    if not isinstance(raw, str) or not 20 <= len(raw) <= 3000:
                        raise ValueError("Invalid ESPHome capture")
                    decoded = convert_capture(bytes.fromhex(raw), self._learn_context)
                    if data.get("status") != "ok" or data.get("ir_code") != decoded["ir_code"] or data.get("key") != decoded["key"]:
                        raise ValueError("ESPHome and HA capture conversion disagree")
                    result.update(raw=raw, length=self._integer(data, "length", 10, 1500))
                self._learn_event(result)
        except (ValueError, TypeError, KeyError, AttributeError) as error:
            pending = self.pending_ir.get(request_id) or self.pending_learn.get(request_id)
            waiter = pending[1] if pending is not None else self.pending.get(self._status_requests.pop(request_id, ""))
            if waiter is not None and not waiter.done():
                waiter.set_exception(ValueError(str(error)))

    async def close(self):
        self._connected = False
        self._connecting = False
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None
        waiters = list(self.pending.values()) + [item[1] for item in self.pending_ir.values()] + [item[1] for item in self.pending_learn.values()]
        for waiter in waiters:
            if not waiter.done():
                waiter.set_exception(OSError("ESPHome transport closed"))
        self._status_requests.clear()
