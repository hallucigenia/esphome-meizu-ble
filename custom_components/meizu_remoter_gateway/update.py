"""HA firmware updates: fixed release feed, device-side download, boot confirmation."""
from __future__ import annotations

import asyncio
from datetime import timedelta
import json
import logging
import time
from uuid import uuid4
from aiohttp import ClientError

from homeassistant.components.update import UpdateDeviceClass, UpdateEntity, UpdateEntityFeature
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import storage
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import CoordinatorEntity, DataUpdateCoordinator, UpdateFailed

from .const import DOMAIN
from .firmware import MANIFEST_URL, authorization, key_bytes, parse_manifest

_LOGGER = logging.getLogger(__name__)


class ReleaseCoordinator(DataUpdateCoordinator):
    def __init__(self, hass):
        super().__init__(hass, _LOGGER, name="Meizu firmware releases", update_interval=timedelta(hours=6))

    async def _async_update_data(self):
        try:
            async with asyncio.timeout(20):
                async with async_get_clientsession(self.hass).get(MANIFEST_URL) as response:
                    response.raise_for_status()
                    body = bytearray()
                    async for chunk in response.content.iter_chunked(4096):
                        body.extend(chunk)
                        if len(body) > 16384:
                            raise ValueError("Firmware manifest is too large")
                    return parse_manifest(json.loads(body))
        except (ClientError, OSError, TimeoutError, ValueError) as error:
            raise UpdateFailed("无法读取魅族网关固件发布清单") from error


async def async_setup_entry(hass, entry, async_add_entities):
    runtime = entry.runtime_data
    if "ota_pull_v1" not in runtime.client.capabilities:
        return
    coordinator = ReleaseCoordinator(hass)
    await coordinator.async_refresh()
    entity = MeizuFirmwareUpdate(coordinator, runtime, entry)
    entity.pending = await entity.store.async_load()
    async_add_entities([entity])


class MeizuFirmwareUpdate(CoordinatorEntity, UpdateEntity):
    _attr_has_entity_name = True
    _attr_translation_key = "firmware"
    _attr_device_class = UpdateDeviceClass.FIRMWARE
    _attr_supported_features = UpdateEntityFeature.INSTALL | UpdateEntityFeature.PROGRESS | UpdateEntityFeature.RELEASE_NOTES

    def __init__(self, coordinator, runtime, entry):
        super().__init__(coordinator)
        self.runtime, self.entry = runtime, entry
        self._attr_unique_id = f"{runtime.serial}_firmware"
        self._attr_device_info = {"identifiers": {(DOMAIN, runtime.serial)}}
        self.store = storage.Store(runtime.hass, 1, f"{DOMAIN}.{entry.entry_id}.ota")
        self.pending = None
        self._install_task = None
        self._installing = False
        self._percentage = None
        self._last_error = None
        self._resumed = False

    @property
    def available(self):
        # A temporary release-server failure must not make the local gateway unavailable.
        return self._installing or (self.runtime.client.connected and "ota_pull_v1" in self.runtime.client.capabilities)

    @property
    def installed_version(self):
        return self.runtime.version

    @property
    def latest_version(self):
        return (self.coordinator.data or {}).get("version")

    @property
    def release_url(self):
        return (self.coordinator.data or {}).get("release_url")

    @property
    def release_summary(self):
        return (self.coordinator.data or {}).get("summary")

    @property
    def in_progress(self):
        return self._installing

    @property
    def update_percentage(self):
        return self._percentage

    @property
    def extra_state_attributes(self):
        state = dict(self.runtime.ota_state)
        return {"phase": state.get("phase"), "safe_mode": state.get("safe_mode", False),
                "rollback_verified": state.get("rollback_verified", False),
                "boot_confirmed": state.get("confirmed", False), "slot": state.get("slot"),
                "ota_key_configured": bool(self.entry.data.get("ota_key")), "last_error": self._last_error,
                "resumed": self._resumed}

    async def async_release_notes(self):
        return self.release_summary

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        self.async_on_remove(self.runtime.subscribe(self.async_write_ha_state))
        if self.pending:
            self._installing = True
            self._last_error = None
            self._install_task = self.entry.async_create_background_task(
                self.hass, self._resume(), "Meizu OTA reconciliation")

    async def _resume(self):
        self._resumed = True
        try:
            await self._monitor()
        except HomeAssistantError as error:
            self._last_error = str(error)
            _LOGGER.warning("Meizu firmware update did not complete: %s", error)
        finally:
            await self._finish_monitor()

    async def async_will_remove_from_hass(self):
        if self._install_task and self._install_task is not asyncio.current_task():
            self._install_task.cancel()
            await asyncio.gather(self._install_task, return_exceptions=True)
        await super().async_will_remove_from_hass()

    async def async_install(self, version, backup, **kwargs):
        if self._installing:
            raise HomeAssistantError("网关已有升级事务，请等待结果")
        if self.pending:
            self._installing = True
            self._install_task = asyncio.current_task()
            try:
                await self._monitor()
            finally:
                self._installing = False
                self._install_task = None
                self.async_write_ha_state()
            return
        release = self.coordinator.data
        if not release or version not in (None, release["version"]):
            raise HomeAssistantError("没有可安装的固件版本，请刷新更新信息")
        key = self.entry.data.get("ota_key", "")
        try:
            key_bytes(key)
        except ValueError as error:
            raise HomeAssistantError(str(error)) from error
        if self.runtime.learning_task is not None or self.runtime.client.ir_lock.locked():
            raise HomeAssistantError("请等待红外学习或发送结束后升级")
        self._installing = True
        self._last_error = None
        self._resumed = False
        self._percentage = 0
        self._install_task = asyncio.current_task()
        image = {key: release[key] for key in ("version", "url", "size", "sha256")}
        image.update(id=uuid4().hex, ts=int(time.time()))
        self.pending = image
        attempted = False
        self.async_write_ha_state()
        try:
            await self.store.async_save(image)
            async with self.runtime.client.ota_lock:
                challenge = (await self.runtime.client.request("ota_challenge"))["data"]["nonce"]
                packet = {**image, "auth": authorization(key, "start", self.runtime.serial, challenge, image)}
                attempted = True
                try:
                    reply = await self.runtime.client.request("ota_start", packet)
                except (OSError, TimeoutError):
                    reply = {"data": {"accepted": True}}
            if not reply.get("data", {}).get("accepted"):
                self.pending = None
                await self.store.async_save(None)
                raise HomeAssistantError("网关拒绝升级：" + str(reply.get("data", {}).get("status", "unknown")))
            await self._monitor()
        except asyncio.CancelledError:
            # HA shutdown does not cancel device-side downloading. The durable
            # request is reconciled on the next setup; never resend automatically.
            raise
        except (OSError, TimeoutError, ValueError, KeyError) as error:
            # A lost ota_start reply is ambiguous; retain its ID for reconciliation.
            self._last_error = str(error)
            if not attempted:
                self.pending = None
                await self.store.async_save(None)
            raise HomeAssistantError("升级连接中断；重连后将按原事务查询结果") from error
        except HomeAssistantError as error:
            self._last_error = str(error)
            raise
        finally:
            self._installing = False
            self._install_task = None
            self.async_write_ha_state()

    async def _monitor(self):
        deadline = time.monotonic() + 480
        missing_since = None
        while time.monotonic() < deadline:
            if self.runtime.client.connected:
                try:
                    state = (await self.runtime.client.ota_status())["data"]
                except (OSError, TimeoutError, ValueError, KeyError):
                    await asyncio.sleep(2)
                    continue
                self.runtime.ota_state = dict(state)
                if state.get("id") == self.pending["id"]:
                    missing_since = None
                    phase = state.get("phase")
                    if phase in ("failed", "cancelled"):
                        self.pending = None
                        await self.store.async_save(None)
                        raise HomeAssistantError("网关升级未完成：" + str(state.get("error", phase)))
                    size, written = self.pending["size"], state.get("written", 0)
                    self._percentage = min(100, max(0, written / size * 100)) if type(written) is int else None
                    if phase == "complete" and state.get("confirmed") is True and state.get("running_version") == self.pending["version"] and state.get("sha256") == self.pending["sha256"]:
                        self.runtime.version = state["running_version"]
                        self.pending = None
                        await self.store.async_save(None)
                        self._percentage = 100
                        self._last_error = None
                        return
                elif not state.get("active") and state.get("confirmed"):
                    missing_since = missing_since or time.monotonic()
                    if time.monotonic() - missing_since > 15:
                        self.pending = None
                        await self.store.async_save(None)
                        raise HomeAssistantError("网关未记录此升级事务，请重新发起升级")
                self.async_write_ha_state()
            await asyncio.sleep(2)
        raise HomeAssistantError("升级结果尚未确认，请检查网关状态；不会自动重复刷写")

    async def _finish_monitor(self):
        self._installing = False
        self._install_task = None
        self.async_write_ha_state()
