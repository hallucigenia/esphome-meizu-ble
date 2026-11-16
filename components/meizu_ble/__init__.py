"""Nonblocking Meizu R16 GATT sensor and acknowledged infrared controller."""
import re
from esphome import automation
import esphome.codegen as cg
from esphome.components import binary_sensor, ble_client, sensor
import esphome.config_validation as cv
from esphome.const import CONF_ID, CONF_TRIGGER_ID
import esphome.final_validate as fv

CODEOWNERS = ["@hallucigenia"]
DEPENDENCIES = ["esp32", "ble_client"]
AUTO_LOAD = ["sensor", "binary_sensor", "md5"]
MULTI_CONF = True

ns = cg.esphome_ns.namespace("meizu_ble")
MeizuBLE = ns.class_("MeizuBLE", cg.PollingComponent, ble_client.BLEClientNode)
MeizuEvent = ns.class_("MeizuEvent")
EventRef = MeizuEvent.operator("ref").operator("const")
EventTrigger = ns.class_("MeizuEventTrigger", automation.Trigger.template(EventRef))
SendAction = ns.class_("SendAction", automation.Action)
LearnAction = ns.class_("LearnAction", automation.Action)
CancelAction = ns.class_("CancelAction", automation.Action)

SENSORS = {
    "temperature": ("°C", 2, "temperature"),
    "humidity": ("%", 2, "humidity"),
    "battery": ("%", 0, "battery"),
    "voltage": ("V", 1, "voltage"),
    "rssi": ("dBm", 0, "signal_strength"),
    "sample_age": ("s", 0, "duration"),
}


def validate_interval(config):
    interval = config["update_interval"].total_milliseconds
    if not 60000 <= interval <= 1800000 or interval % 60000:
        raise cv.Invalid("Meizu battery polling interval must be a whole number of minutes from 1min to 30min")
    return config


def validate_service(value):
    value = cv.string_strict(value)
    if re.fullmatch(r"meizu_[a-z0-9_]+", value) is None:
        raise cv.Invalid("api_service must start with meizu_ and contain lowercase letters, digits or underscores")
    return value


CONFIG_SCHEMA = cv.All(
    cv.Schema({
        cv.GenerateID(): cv.declare_id(MeizuBLE),
        cv.Optional("api_service"): validate_service,
        cv.Optional("available"): binary_sensor.binary_sensor_schema(),
        cv.Optional("on_event"): automation.validate_automation({cv.GenerateID(CONF_TRIGGER_ID): cv.declare_id(EventTrigger)}),
        **{cv.Optional(key): sensor.sensor_schema(
            unit_of_measurement=unit, accuracy_decimals=decimals, device_class=device_class,
            state_class="measurement", **({"entity_category": "diagnostic"} if key not in {"temperature", "humidity"} else {}),
        ) for key, (unit, decimals, device_class) in SENSORS.items()},
    }).extend(ble_client.BLE_CLIENT_SCHEMA).extend(cv.polling_component_schema("5min")),
    validate_interval,
)


def final_validate(config):
    full = fv.full_config.get()
    if full["esp32"]["framework"]["type"] != "esp-idf":
        raise cv.Invalid("meizu_ble currently supports the ESP-IDF framework")
    path = full.get_path_for_id(config["ble_client_id"])[:-1]
    parent = full.get_config_for_path(path)
    if parent["auto_connect"]:
        raise cv.Invalid("Set auto_connect: false on the dedicated Meizu ble_client")

    def references(value):
        if isinstance(value, dict):
            return int(value.get("ble_client_id") == config["ble_client_id"]) + sum(references(v) for v in value.values())
        if isinstance(value, list):
            return sum(references(v) for v in value)
        return 0

    if references(full) != 1:
        raise cv.Invalid("A Meizu ble_client must be dedicated to exactly one meizu_ble component")
    if "api_service" in config:
        api = full.get("api") or {}
        if not api.get("custom_services") or not api.get("homeassistant_services"):
            raise cv.Invalid("api_service requires api.custom_services: true and api.homeassistant_services: true")
        names = [entry.get("api_service") for entry in full.get("meizu_ble", [])]
        if names.count(config["api_service"]) != 1:
            raise cv.Invalid("Meizu api_service names must be unique")
    return config


FINAL_VALIDATE_SCHEMA = final_validate


async def to_code(config):
    var = cg.new_Pvariable(config[CONF_ID])
    await cg.register_component(var, config)
    await ble_client.register_ble_node(var, config)
    for name in SENSORS:
        if name in config:
            entity = await sensor.new_sensor(config[name])
            cg.add(getattr(var, f"set_{name}_sensor")(entity))
    if "available" in config:
        cg.add(var.set_available_sensor(await binary_sensor.new_binary_sensor(config["available"])))
    if "api_service" in config:
        cg.add_define("USE_MEIZU_BLE_API")
        cg.add(var.set_api_service(config["api_service"]))
    for entry in config.get("on_event", []):
        trigger = cg.new_Pvariable(entry[CONF_TRIGGER_ID], var)
        await automation.build_automation(trigger, [(EventRef, "event")], entry)


@automation.register_action("meizu_ble.send", SendAction, cv.Schema({
    cv.Required(CONF_ID): cv.use_id(MeizuBLE),
    cv.Required("request_id"): cv.templatable(cv.string_strict),
    cv.Required("key"): cv.templatable(cv.sensitive(cv.string_strict)),
    cv.Optional("ir_code", default=""): cv.templatable(cv.sensitive(cv.string_strict)),
}), synchronous=True)
async def send_action(config, action_id, template_arg, args):
    var = cg.new_Pvariable(action_id, template_arg, await cg.get_variable(config[CONF_ID]))
    for name in ("request_id", "key", "ir_code"):
        cg.add(getattr(var, f"set_{name}")(await cg.templatable(config[name], args, cg.std_string)))
    return var


@automation.register_action("meizu_ble.learn", LearnAction, cv.Schema({
    cv.Required(CONF_ID): cv.use_id(MeizuBLE),
    cv.Required("request_id"): cv.templatable(cv.string_strict),
    cv.Required("device"): cv.templatable(cv.string_strict),
    cv.Optional("timeout", default=15): cv.templatable(cv.int_range(min=1, max=15)),
}), synchronous=True)
async def learn_action(config, action_id, template_arg, args):
    var = cg.new_Pvariable(action_id, template_arg, await cg.get_variable(config[CONF_ID]))
    for name in ("request_id", "device", "timeout"):
        value_type = cg.uint32 if name == "timeout" else cg.std_string
        cg.add(getattr(var, f"set_{name}")(await cg.templatable(config[name], args, value_type)))
    return var


@automation.register_action("meizu_ble.cancel", CancelAction, cv.Schema({
    cv.Required(CONF_ID): cv.use_id(MeizuBLE), cv.Required("request_id"): cv.templatable(cv.string_strict),
}), synchronous=True)
async def cancel_action(config, action_id, template_arg, args):
    var = cg.new_Pvariable(action_id, template_arg, await cg.get_variable(config[CONF_ID]))
    cg.add(var.set_request_id(await cg.templatable(config["request_id"], args, cg.std_string)))
    return var
