"""Shared identity and push subscriptions for Meizu entities."""

from homeassistant.helpers.entity import DeviceInfo, Entity

from .const import DOMAIN


class MeizuEntity(Entity):
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, runtime, address, suffix):
        self.runtime, self.address = runtime, address
        self._attr_unique_id = f"{runtime.serial}_{address.replace(':', '')}_{suffix}"

    @property
    def device_info(self):
        info = self.runtime.devices[self.address].get("status", {})
        return DeviceInfo(
            identifiers={(DOMAIN, self.address)}, connections={("bluetooth", self.address)},
            name=f"魅族遥控器 {self.address[-5:].replace(':', '').upper()}",
            manufacturer=info.get("manufacturer") or "Meizu", model=info.get("model"),
            sw_version=info.get("fireware"), via_device=(DOMAIN, self.runtime.serial),
        )

    @property
    def available(self):
        return self.runtime.available(self.address)

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        self.async_on_remove(self.runtime.subscribe(self.async_write_ha_state))
