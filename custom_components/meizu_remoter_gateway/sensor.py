"""Measured temperature, humidity, battery, and diagnostics."""

from datetime import datetime, timezone

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.const import EntityCategory, PERCENTAGE, SIGNAL_STRENGTH_DECIBELS_MILLIWATT, UnitOfElectricPotential, UnitOfTemperature
from homeassistant.core import callback

from .client import measurement
from .entity import MeizuEntity

SENSORS = {
    "temperature": ("温度", SensorDeviceClass.TEMPERATURE, UnitOfTemperature.CELSIUS, None),
    "humidity": ("湿度", SensorDeviceClass.HUMIDITY, PERCENTAGE, None),
    "battery": ("电量", SensorDeviceClass.BATTERY, PERCENTAGE, EntityCategory.DIAGNOSTIC),
    "voltage": ("电池电压", SensorDeviceClass.VOLTAGE, UnitOfElectricPotential.VOLT, EntityCategory.DIAGNOSTIC),
    "rssi": ("蓝牙信号", SensorDeviceClass.SIGNAL_STRENGTH, SIGNAL_STRENGTH_DECIBELS_MILLIWATT, EntityCategory.DIAGNOSTIC),
    "last_measurement": ("最近测量", SensorDeviceClass.TIMESTAMP, None, EntityCategory.DIAGNOSTIC),
}


async def async_setup_entry(hass, entry, async_add_entities):
    runtime = entry.runtime_data
    known = set()

    @callback
    def discover():
        additions = []
        for address in runtime.devices.keys() - known:
            known.add(address)
            additions.extend(MeizuSensor(runtime, address, key) for key in SENSORS)
        if additions:
            async_add_entities(additions)

    entry.async_on_unload(runtime.subscribe(discover))
    discover()


class MeizuSensor(MeizuEntity, SensorEntity):
    def __init__(self, runtime, address, key):
        super().__init__(runtime, address, key)
        self.key = key
        name, device_class, unit, category = SENSORS[key]
        self._attr_name = name
        self._attr_device_class = device_class
        self._attr_native_unit_of_measurement = unit
        self._attr_entity_category = category
        if key != "last_measurement":
            self._attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self):
        data = self.runtime.devices[self.address]
        if self.key == "last_measurement":
            stamp = data.get("last_measurement")
            return datetime.fromtimestamp(stamp, timezone.utc) if stamp is not None else None
        return measurement(data.get("status", {}), self.key)
