"""Configure a gateway by its verified IP and serial number."""

import asyncio
import math
import time
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import selector

from .client import GatewayClient
from .const import DEFAULT_PORT, DOMAIN
from .firmware import authorization, key_bytes
from .esphome_client import ESPHomeGatewayClient
from .client import normalize_address


async def verify_ota_key(client, serial, key):
    key_bytes(key)
    if "ota_pull_v1" not in client.capabilities:
        raise ValueError("OTA is not supported by this firmware")
    image = {"id": "", "ts": int(time.time())}
    async with client.ota_lock:
        nonce = (await client.request("ota_challenge"))["data"]["nonce"]
        result = await client.request("ota_auth", {**image, "auth": authorization(key, "check", serial, nonce, image)})
    if not result.get("data", {}).get("accepted"):
        raise ValueError("OTA authentication failed")


class MeizuGatewayFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return MeizuLearningFlow()

    async def async_step_reconfigure(self, user_input=None):
        if user_input is not None and "host" in user_input:
            return await self.async_step_tcp_reconfigure(user_input)
        return self.async_show_menu(step_id="reconfigure", menu_options=["tcp_reconfigure", "esphome"])

    async def async_step_tcp_reconfigure(self, user_input=None):
        entry = self._get_reconfigure_entry()
        errors = {}
        if user_input is not None:
            client = GatewayClient(user_input["host"], user_input["port"], lambda message: None)
            try:
                info = await client.connect()
                if info["serialno"].upper() != entry.data["serialno"].upper():
                    return self.async_abort(reason="wrong_gateway")
                entered_key = user_input.get("ota_key", "").strip()
                key = entered_key or entry.data.get("ota_key", "")
                if key and (entered_key or "ota_pull_v1" in client.capabilities):
                    await verify_ota_key(client, info["serialno"].upper(), key)
                return self.async_update_reload_and_abort(entry, data_updates={
                    "transport": "tcp", "host": user_input["host"], "port": user_input["port"], **({"ota_key": key} if key else {})})
            except (OSError, TimeoutError, ValueError, KeyError):
                errors["base"] = "cannot_authenticate"
            finally:
                await client.close()
        return self.async_show_form(step_id="tcp_reconfigure", errors=errors, data_schema=vol.Schema({
            vol.Required("host", default=entry.data.get("host", "")): str,
            vol.Required("port", default=entry.data.get("port", DEFAULT_PORT)): vol.All(vol.Coerce(int), vol.Range(min=1, max=65535)),
            vol.Optional("ota_key"): selector.TextSelector(selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)),
        }))

    async def async_step_user(self, user_input=None):
        if user_input is not None:
            return await self.async_step_tcp(user_input)
        return self.async_show_menu(step_id="user", menu_options=["tcp", "esphome"])

    async def async_step_tcp(self, user_input=None):
        errors = {}
        if user_input is not None:
            client = GatewayClient(user_input["host"], user_input["port"], lambda message: None)
            try:
                info = await client.connect()
                await self.async_set_unique_id(info["serialno"].upper())
                self._abort_if_unique_id_configured(updates=user_input)
                return self.async_create_entry(title=f"魅族网关 {info['serialno'].upper()}",
                    data={**user_input, "serialno": info["serialno"].upper()})
            except (OSError, TimeoutError, ValueError):
                errors["base"] = "cannot_connect"
            finally:
                await client.close()
        return self.async_show_form(step_id="tcp", errors=errors, data_schema=vol.Schema({
            vol.Required("host", default=(user_input or {}).get("host", "")): str,
            vol.Required("port", default=(user_input or {}).get("port", DEFAULT_PORT)): vol.All(vol.Coerce(int), vol.Range(min=1, max=65535)),
        }))

    async def async_step_esphome(self, user_input=None):
        reconfigure = getattr(self, "context", {}).get("source") == "reconfigure"
        entry = self._get_reconfigure_entry() if reconfigure else None
        errors = {}
        if user_input is not None:
            client = None
            try:
                settings = {**user_input, "transport": "esphome", "address": normalize_address(user_input["address"])}
                settings["serialno"] = entry.data["serialno"] if entry else (
                    "ESP_" + settings["esphome_device_id"].upper() + "_" + settings["address"].replace(":", "").upper())
                client = ESPHomeGatewayClient(self.hass, settings, lambda message: None)
                await client.connect()
                if entry is not None:
                    # Keep the config-entry ID and serial: the Store and remote identity remain stable.
                    return self.async_update_reload_and_abort(entry, data_updates=settings)
                await self.async_set_unique_id(settings["serialno"])
                self._abort_if_unique_id_configured(updates=settings)
                return self.async_create_entry(title="魅族 ESPHome", data=settings)
            except (OSError, TimeoutError, ValueError, KeyError):
                errors["base"] = "cannot_connect_esphome"
            finally:
                if client is not None:
                    await client.close()
        previous = {**(entry.data if entry else {}), **(user_input or {})}
        return self.async_show_form(step_id="esphome", errors=errors, data_schema=vol.Schema({
            vol.Required("esphome_device_id", default=previous.get("esphome_device_id", "")): selector.DeviceSelector(
                selector.DeviceSelectorConfig(integration="esphome")),
            vol.Required("esphome_service", default=previous.get("esphome_service", "")): str,
            vol.Required("api_source", default=previous.get("api_source", "meizu_puck")): str,
            vol.Required("address", default=previous.get("address", "")): str,
        }))


class MeizuLearningFlow(config_entries.OptionsFlow):
    """A native sequential wizard; commands stay in Store, never in options."""

    def __init__(self):
        self._task = None
        self._ticker = None
        self._view = {}
        self._selection = {}
        self._result = None
        self._removed = False

    @property
    def runtime(self):
        return self.config_entry.runtime_data

    async def async_step_init(self, user_input=None):
        if "learn_v1" not in self.runtime.client.capabilities:
            return self.async_abort(reason="learning_unsupported")
        if not self.runtime.client.connected or not self.runtime.devices:
            return self.async_abort(reason="gateway_unavailable")
        if self.runtime.learning_task is not None:
            return self.async_abort(reason="learning_busy")
        errors = {}
        if user_input is not None:
            device, command = user_input["device"].strip(), user_input["command"].strip()
            address = user_input["address"]
            if not 1 <= len(device) <= 80 or not 1 <= len(command) <= 80:
                errors["base"] = "invalid_name"
            elif command in self.runtime.commands.get(address, {}).get(device, {}) and not user_input.get("replace"):
                errors["replace"] = "already_saved"
            else:
                self._selection = {"address": address, "device": device, "command": command}
                self._task = None
                return await self.async_step_learn()
        previous = {**self._selection, **(user_input or {})}
        addresses = sorted(self.runtime.devices)
        return self.async_show_form(step_id="init", errors=errors, data_schema=vol.Schema({
            vol.Required("address", default=previous.get("address", addresses[0])): selector.SelectSelector(
                selector.SelectSelectorConfig(options=[{"value": address, "label": f"魅族小圆饼 {address[-5:].upper()}"} for address in addresses])),
            vol.Required("device", default=previous.get("device", "hdmi_switch")): str,
            vol.Required("command", default=previous.get("command", "")): str,
            vol.Optional("replace", default=False): bool,
        }))

    @callback
    def _progress(self, state):
        self._view = state
        if not self._removed:
            self.async_notify_flow_changed()

    async def _countdown(self):
        while True:
            await asyncio.sleep(1)
            if deadline := self._view.get("deadline"):
                self.async_update_progress(min(.95, max(0, 1 - (deadline - time.time()) / 15)))
                self.async_notify_flow_changed()

    async def _learn(self):
        self._view = {"phase": "connecting"}
        self._ticker = self.hass.async_create_task(self._countdown(), "Meizu learning countdown")
        try:
            await self.runtime.learn_command(self._selection["address"], self._selection["device"],
                                             self._selection["command"], progress=self._progress)
            return {"ok": True, "message": "按键已学习并保存。可以试发验证，或继续学习下一键。"}
        except HomeAssistantError as error:
            return {"ok": False, "message": f"学习未完成：{error}。原有按键已保留。"}
        finally:
            self._ticker.cancel()
            await asyncio.gather(self._ticker, return_exceptions=True)

    async def async_step_learn(self, user_input=None):
        if self._task is None:
            self._task = self.hass.async_create_task(self._learn(), "Meizu native learning")
        if self._task.done():
            self._result = self._task.result()
            return self.async_show_progress_done(next_step_id="result")
        phase = self._view.get("phase", "connecting")
        remaining = max(0, math.ceil(self._view.get("deadline", time.time()) - time.time()))
        return self.async_show_progress(step_id="learn", progress_action=(
            "receiving" if phase == "receiving" else "listening" if phase == "ready" else "connecting"),
            progress_task=self._task, description_placeholders={
                "command": self._selection["command"], "remaining": str(remaining)})

    async def async_step_result(self, user_input=None):
        if user_input is not None:
            action = user_input["action"]
            if action == "finish":
                return self.async_create_entry(title="", data=dict(self.config_entry.options))
            if action == "next":
                self._selection["command"] = ""
                return await self.async_step_init()
            if action == "retry":
                self._task = None
                return await self.async_step_learn()
            if action == "test" and self._result["ok"]:
                try:
                    await self.runtime.send_command(self._selection["address"], self._selection["device"], self._selection["command"])
                    self._result["message"] = "已发送，小圆饼已应答。请观察目标设备是否响应；也可以继续学习下一键。"
                except HomeAssistantError as error:
                    self._result["message"] = f"按键已保存，试发未成功：{error}。"
        actions = (["next", "test", "retry", "finish"] if self._result["ok"] else ["retry", "next", "finish"])
        return self.async_show_form(step_id="result", description_placeholders={
            "command": self._selection["command"], "result": self._result["message"]}, data_schema=vol.Schema({
                vol.Required("action", default=actions[0]): selector.SelectSelector(
                    selector.SelectSelectorConfig(options=actions, translation_key="learn_action"))}))

    @callback
    def async_remove(self):
        self._removed = True
        for task in (self._task, self._ticker):
            if task is not None and not task.done():
                task.cancel()
