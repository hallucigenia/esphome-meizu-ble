"""Local push integration for the Meizu ESP32 gateway."""

from __future__ import annotations

import asyncio
from copy import deepcopy
import json
from datetime import timedelta
import logging
import time

import voluptuous as vol

from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.core import SupportsResponse, callback
from homeassistant.exceptions import ConfigEntryNotReady, HomeAssistantError, ServiceValidationError
from homeassistant.helpers import device_registry as dr, storage
from homeassistant.helpers.event import async_track_time_interval

from .client import GatewayClient, ir_message, measurement, normalize_address
from .codec import convert_capture
from .esphome_client import ESPHomeGatewayClient
from .const import DOMAIN, PLATFORMS

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass, entry):
    runtime = GatewayRuntime(hass, entry)
    try:
        await runtime.connect()
    except (OSError, TimeoutError, ValueError) as error:
        await runtime.client.close()
        raise ConfigEntryNotReady(str(error)) from error
    entry.runtime_data = runtime
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = runtime
    runtime.commands = await runtime.store.async_load() or {}
    try:
        await hass.config_entries.async_forward_entry_setups(entry, runtime.platforms)
    except BaseException:
        await runtime.stop()
        hass.data[DOMAIN].pop(entry.entry_id, None)
        raise
    runtime.task = entry.async_create_background_task(hass, runtime.run(), "Meizu gateway connection")
    entry.async_on_unload(async_track_time_interval(hass, runtime.tick, timedelta(seconds=30)))
    entry.async_on_unload(hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, runtime.stop))
    _register_services(hass)
    return True


async def async_unload_entry(hass, entry):
    if not await hass.config_entries.async_unload_platforms(entry, entry.runtime_data.platforms):
        return False
    await entry.runtime_data.stop()
    hass.data[DOMAIN].pop(entry.entry_id)
    if not hass.data[DOMAIN]:
        hass.data.pop(DOMAIN)
        for name in ("bind", "set_update_interval", "send_ir", "store_command", "cancel_learning"):
            hass.services.async_remove(DOMAIN, name)
    return True


def _register_services(hass):
    if hass.services.has_service(DOMAIN, "send_ir"):
        return

    async def handle(call):
        data = dict(call.data)
        serial = data.pop("serial_no").upper()
        runtime = next((r for r in hass.data.get(DOMAIN, {}).values() if r.serial == serial), None)
        if runtime is None:
            raise ServiceValidationError("未找到此魅族网关")
        try:
            if call.service == "cancel_learning":
                return {"cancelled": await runtime.cancel_learning()}
            if call.service == "bind":
                reply = await runtime.client.request("bind")
                if str(reply.get("data", {}).get("status")) != "1":
                    raise ServiceValidationError("网关当前无法进入绑定模式，请稍后重试")
                return {"binding_started": True}
            if call.service == "set_update_interval":
                await runtime.client.request("setinterval", data)
                return {"update_interval": runtime.interval}
            address = normalize_address(data.pop("address"))
            packet = ir_message(address, data.pop("key"), data.pop("ir_code", None), max_bytes=getattr(runtime.client, "max_message_bytes", 511))
            if call.service == "store_command":
                await runtime.save_command(address, data["device"], data["command"], packet["data"])
                return {"stored": True, "device": data["device"], "command": data["command"]}
            if address not in runtime.devices:
                raise ServiceValidationError("此遥控器尚未绑定网关")
            return await runtime.transmit(address, packet)
        except (ValueError, OSError, TimeoutError) as error:
            raise HomeAssistantError(str(error)) from error

    gateway = {vol.Required("serial_no"): str}
    ir = {**gateway, vol.Required("address"): str, vol.Required("key"): str, vol.Optional("ir_code"): str}
    for name, schema in (
        ("bind", gateway),
        ("cancel_learning", gateway),
        ("set_update_interval", {**gateway, vol.Required("update_interval"): vol.All(vol.Coerce(int), vol.Range(min=1, max=30))}),
        ("send_ir", ir),
        ("store_command", {**ir, vol.Required("device"): vol.All(str, vol.Length(min=1, max=80)), vol.Required("command"): vol.All(str, vol.Length(min=1, max=80))}),
    ):
        hass.services.async_register(DOMAIN, name, handle, schema=vol.Schema(schema), supports_response=SupportsResponse.OPTIONAL)


class GatewayRuntime:
    def __init__(self, hass, entry):
        self.hass, self.entry = hass, entry
        self.transport = entry.data.get("transport", "tcp")
        self.platforms = [platform for platform in PLATFORMS if platform == "remote"] if self.transport == "esphome" else PLATFORMS
        self.serial = entry.data["serialno"].upper()
        self.interval = 5
        self.version = None
        self.ota_state = {}
        self.devices = {}
        self.commands = {}
        self.last_ir_results = {}
        self.listeners = set()
        self.task = None
        self.closed = False
        self.command_lock = asyncio.Lock()
        self.learning_task = None
        self.learning = None
        self.store = storage.Store(hass, 1, f"{DOMAIN}.{entry.entry_id}.commands")
        self.client = ESPHomeGatewayClient(hass, entry.data, self.message) if self.transport == "esphome" else GatewayClient(entry.data["host"], entry.data["port"], self.message)

    async def connect(self):
        info = await self.client.connect()
        if info["serialno"].upper() != self.serial:
            await self.client.close()
            raise ValueError("Gateway serial number changed")
        self.version = info.get("version")
        self.ota_state = dict(info.get("ota", {}))
        self.interval = info.get("update_interval", 5)
        dr.async_get(self.hass).async_get_or_create(
            config_entry_id=self.entry.entry_id, identifiers={(DOMAIN, self.serial)},
            manufacturer="ESP32", model="Meizu BLE gateway", name=f"魅族蓝牙网关 {self.serial}",
            sw_version=self.version,
        )
        await self.client.send({"type": "subscribe"})
        await self.client.request("devices")
        self.notify()

    async def run(self):
        while not self.closed:
            try:
                if not self.client.connected:
                    for device in self.devices.values():
                        device["last_measurement"] = None
                    await self.client.close()
                    await self.connect()
                await asyncio.sleep(30)
                await self.client.heartbeat()
                if "ota_pull_v1" in self.client.capabilities:
                    await self.client.ota_status()
            except (OSError, TimeoutError, ValueError) as error:
                _LOGGER.warning("Meizu gateway %s connection failed: %s", self.serial, error)
                await self.client.close()
                # Cached values cannot survive a disconnect as fresh samples.
                for device in self.devices.values():
                    device["last_measurement"] = None
                self.notify()
                await asyncio.sleep(5)

    async def stop(self, _event=None):
        self.closed = True
        await self.cancel_learning()
        if self.task is not None:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
        await self.client.close()

    async def cancel_learning(self):
        task = self.learning_task
        if task is None:
            return False
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        return True

    @callback
    def subscribe(self, listener):
        self.listeners.add(listener)
        return lambda: self.listeners.discard(listener)

    @callback
    def notify(self):
        for listener in tuple(self.listeners):
            listener()

    @callback
    def tick(self, _now):
        self.notify()

    @callback
    def message(self, message):
        data = message.get("data", {})
        kind = message["type"]
        if kind == "setinterval":
            self.interval = data.get("update_interval", self.interval)
        elif kind == "ota_status":
            self.ota_state = dict(data)
        elif kind == "devices":
            for item in data.get("devices", []):
                self.update_device(item, fresh=False)
        elif kind == "update":
            self.update_device(data, fresh=True)
        elif kind == "removebind":
            try:
                address = normalize_address(data.get("device", ""))
            except ValueError:
                return
            if address in self.devices:
                self.devices[address]["available"] = False
        self.notify()

    def update_device(self, data, *, fresh):
        try:
            address = normalize_address(data["device"])
        except (KeyError, ValueError):
            return
        previous = self.devices.get(address, {})
        stamp = previous.get("last_measurement")
        if fresh and data.get("available") == 1:
            stamp = time.time()
        elif not fresh and "measurement_age_v1" in getattr(self.client, "capabilities", set()):
            age = measurement(data, "measurement_age_ms")
            now = time.time()
            # Recover the actual sample time, including its age before HA
            # reconnected. A cached list never becomes a new measurement.
            stamp = now - age / 1000 if age is not None and 0 <= age <= now * 1000 else None
        self.devices[address] = {**previous, **data, "status": {**previous.get("status", {}), **data.get("status", {})},
                                 "last_measurement": stamp}
        info = self.devices[address]["status"]
        if self.hass is not None and any(info.get(key) for key in ("manufacturer", "model", "fireware")):
            details = {key: info[field] for key, field in (("manufacturer", "manufacturer"), ("model", "model"), ("sw_version", "fireware"))
                       if isinstance(info.get(field), str) and info[field]}
            dr.async_get(self.hass).async_get_or_create(config_entry_id=self.entry.entry_id,
                identifiers={(DOMAIN, address)}, connections={("bluetooth", address)}, via_device=(DOMAIN, self.serial), **details)

    def available(self, address, *, fresh=True):
        if not fresh and self.transport == "esphome":
            # Remote availability describes the control path, not a fresh sensor sample.
            # A manual request can reconnect the resting puck and must still get its real ACK.
            return self.client.connected and address in self.devices
        device = self.devices.get(address, {})
        last = device.get("last_measurement")
        return self.client.connected and device.get("available") == 1 and (
            not fresh or last is not None and time.time() - last < self.interval * 120 + 90)

    async def save_command(self, address, device, name, packet, metadata=None):
        async with self.command_lock:
            commands = deepcopy(self.commands)
            commands.setdefault(address, {}).setdefault(device, {})[name] = {
                "key": packet["key"], **({"ir_code": packet["ircode"]} if "ircode" in packet else {}),
                **(metadata or {})}
            await self.store.async_save(commands)
            self.commands = commands
        self.notify()

    async def learn_command(self, address, device, name, *, timeout=15, progress=None):
        if not isinstance(device, str) or not 1 <= len(device.strip()) <= 80 or not isinstance(name, str) or not 1 <= len(name.strip()) <= 80:
            raise ServiceValidationError("设备名和按键名须为 1–80 个字符")
        address = normalize_address(address)
        if address not in self.devices or not self.client.connected:
            raise HomeAssistantError("魅族遥控器尚未绑定或网关不可用")
        if self.learning_task is not None:
            raise HomeAssistantError("此网关正在学习另一按键")
        self.learning_task = asyncio.current_task()
        self.learning = {"device": device, "command": name, "address": address, "phase": "connecting"}

        def update(data):
            # The worker may start before its queued acknowledgement is sent.
            ranks = {"queued": 0, "connecting": 1, "ready": 2, "receiving": 3}
            if ranks.get(data["phase"], -1) < ranks.get(self.learning["phase"], -1):
                return
            self.learning.update({key: value for key, value in data.items() if key in ("phase", "received", "length")})
            if data["phase"] == "ready":
                self.learning["deadline"] = time.time() + data["timeout_ms"] / 1000
            self.notify()
            if progress:
                progress(dict(self.learning))

        try:
            if self.transport == "esphome":
                raw = await self.client.learn_ir(address, timeout, update, device=device)
            else:
                raw = await self.client.learn_ir(address, timeout, update)
            learned = convert_capture(raw, device)
            limit = getattr(self.client, "max_message_bytes", 511)
            packet = ir_message(address, learned["key"], learned["ir_code"], max_bytes=limit)
            # Also reserve the correlation ID used at send time, so a learned
            # long code is never saved if it cannot pass the current transport.
            if len(json.dumps({**packet, "data": {**packet["data"], "request_id": "0" * 32}}, separators=(",", ":"))) > limit:
                raise ValueError("此红外码超出当前网关传输长度，旧按键已保留")
            await self.save_command(address, device, name, packet["data"], {
                key: value for key, value in learned.items() if key not in ("key", "ir_code")
            } | {"learned_at": time.time()})
            self.learning["phase"] = "saved"
            return dict(self.learning)
        except asyncio.CancelledError:
            self.learning["phase"] = "cancelled"
            raise
        except (OSError, TimeoutError, ValueError) as error:
            self.learning.update(phase="failed", error=str(error))
            raise HomeAssistantError(str(error)) from error
        finally:
            self.learning_task = None
            self.notify()

    async def send_command(self, address, device, name):
        try:
            command = self.commands[address][device][name]
        except KeyError as error:
            raise ServiceValidationError(f"尚未采集红外码：{device}/{name}") from error
        if not self.available(address, fresh=False):
            raise HomeAssistantError("魅族遥控器或蓝牙网关不可用")
        return await self.transmit(address, ir_message(address, command["key"], command.get("ir_code"), max_bytes=getattr(self.client, "max_message_bytes", 511)))

    async def transmit(self, address, packet):
        try:
            result = await self.client.send_ir(packet)
        except (OSError, TimeoutError, ValueError) as error:
            self.last_ir_results[address] = {"confirmed": False, "bridge_acknowledged": False,
                                             "status": "failed", "error": str(error), "at": time.time()}
            self.notify()
            raise HomeAssistantError(str(error)) from error
        self.last_ir_results[address] = {**result, "at": time.time()}
        self.notify()
        return result
