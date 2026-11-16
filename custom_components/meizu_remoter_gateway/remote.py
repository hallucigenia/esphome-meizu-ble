"""Native named IR learning, sending and deletion."""

import asyncio
from copy import deepcopy

from homeassistant.components.remote import RemoteEntity, RemoteEntityFeature
from homeassistant.components import persistent_notification
from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

from .entity import MeizuEntity


async def async_setup_entry(hass, entry, async_add_entities):
    runtime = entry.runtime_data
    known = set()

    @callback
    def discover():
        additions = []
        for address in runtime.devices.keys() - known:
            known.add(address)
            additions.append(MeizuRemote(runtime, address))
        if additions:
            async_add_entities(additions)

    entry.async_on_unload(runtime.subscribe(discover))
    discover()


class MeizuRemote(MeizuEntity, RemoteEntity):
    _attr_name = "红外遥控"
    _attr_supported_features = RemoteEntityFeature.DELETE_COMMAND
    _attr_is_on = True

    def __init__(self, runtime, address):
        super().__init__(runtime, address, "remote")

    @property
    def available(self):
        return self.runtime.available(self.address, fresh=False)

    @property
    def supported_features(self):
        features = RemoteEntityFeature.DELETE_COMMAND
        if "learn_v1" in self.runtime.client.capabilities:
            features |= RemoteEntityFeature.LEARN_COMMAND
        return features

    @property
    def extra_state_attributes(self):
        return {"gateway_serial": self.runtime.serial, "bluetooth_address": self.address,
                "commands": {device: sorted(names) for device, names in self.runtime.commands.get(self.address, {}).items()},
                "transmission_confirmation": self.runtime.client.ir_confirmation,
                "last_ir_result": self.runtime.last_ir_results.get(self.address),
                "learning_transport": getattr(self.runtime, "transport", "tcp"),
                "learning_over_tcp": "learn_v1" in self.runtime.client.capabilities and getattr(self.runtime, "transport", "tcp") == "tcp",
                "learning": dict(self.runtime.learning) if self.runtime.learning and self.runtime.learning.get("address") == self.address else None}

    async def async_learn_command(self, **kwargs):
        if kwargs.get("command_type", "ir") != "ir" or kwargs.get("alternative", False):
            raise ServiceValidationError("当前支持普通红外按键学习")
        device = kwargs.get("device")
        commands = kwargs.get("command", [])
        if not device or not commands:
            raise ServiceValidationError("请填写设备名和按键名")
        if isinstance(commands, str):
            commands = [commands]
        timeout = kwargs.get("timeout", 15)
        if type(timeout) not in (int, float) or not 1 <= timeout <= 15 or int(timeout) != timeout:
            raise ServiceValidationError("红外学习窗口为 1–15 秒")
        notification = f"meizu_learn_{self.runtime.serial}"
        for command in commands:
            def progress(state):
                if state["phase"] == "ready":
                    persistent_notification.async_create(self.hass,
                        f"请短按原遥控器的「{command}」。对准小圆饼顶部中心，距离小于 1 厘米。学习窗口 {int(timeout)} 秒。",
                        title=f"学习 {device} / {command}", notification_id=notification)
            try:
                await self.runtime.learn_command(self.address, device, command, timeout=int(timeout), progress=progress)
            except asyncio.CancelledError as error:
                # Cancelling the capture must still finish HA's service request.
                raise HomeAssistantError("学习已取消") from error
            finally:
                persistent_notification.async_dismiss(self.hass, notification)

    async def async_turn_on(self, **kwargs):
        raise ServiceValidationError("请使用 remote.send_command 发送具体按键")

    async def async_turn_off(self, **kwargs):
        raise ServiceValidationError("请使用 remote.send_command 发送具体按键")

    async def async_send_command(self, command, **kwargs):
        if kwargs.get("hold_secs", 0):
            raise ServiceValidationError("此固件不支持按住按键")
        device = kwargs.get("device", "hdmi_switch")
        commands = list(command)
        # Check the entire sequence before emitting any infrared.
        saved = self.runtime.commands.get(self.address, {}).get(device, {})
        for name in commands:
            if name not in saved:
                raise ServiceValidationError(f"尚未采集红外码：{device}/{name}")
        repeats = kwargs.get("num_repeats", 1)
        delay = kwargs.get("delay_secs", .4)
        for repeat in range(repeats):
            for index, name in enumerate(commands):
                if repeat or index:
                    await asyncio.sleep(delay)
                await self.runtime.send_command(self.address, device, name)

    async def async_delete_command(self, **kwargs):
        device = kwargs.get("device", "hdmi_switch")
        async with self.runtime.command_lock:
            commands = deepcopy(self.runtime.commands)
            saved = commands.get(self.address, {}).get(device, {})
            for command in kwargs.get("command", []):
                saved.pop(command, None)
            await self.runtime.store.async_save(commands)
            self.runtime.commands = commands
        self.runtime.notify()
