"""friday.set_wifi: route a Wi-Fi change to the satellite behind a Home Assistant device."""

from __future__ import annotations

import voluptuous as vol

from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv, device_registry as dr

from .const import DEVICE_KIND_SATELLITE, DOMAIN, OPTIONS_GENERAL
from .general import SCOPE_ADVANCED, SCOPE_GENERAL, async_apply_general_all, log_settings_change
from .settings import (
    GENERAL_KEYS, SettingError, check_wakewords, validate_advanced, validate_general,
)
from .wifi import async_set_wifi

SERVICE_SET_WIFI = "set_wifi"
SCHEMA = vol.Schema({
    vol.Required("device_id"): cv.string,
    vol.Required("ssid"): vol.All(cv.string, vol.Length(min=1, max=32)),
    vol.Required("password"): vol.All(cv.string, vol.Length(min=8, max=63)),
})


def find_satellite(hass: HomeAssistant, device_id: str):
    """The satellite connection that owns the HA device, or a ServiceValidationError."""
    device = dr.async_get(hass).async_get(device_id)
    friday_id = next((i[1] for i in device.identifiers if i[0] == DOMAIN), None) if device else None
    if friday_id is None:
        raise ServiceValidationError("Not a F.R.I.D.A.Y. device.")
    for entry in hass.config_entries.async_entries(DOMAIN):
        for connection in getattr(entry, "runtime_data", {}).values():
            hello = connection.hello
            if hello is not None and hello.device.get("id") == friday_id:
                if hello.device.get("kind") != DEVICE_KIND_SATELLITE:
                    raise ServiceValidationError("Only satellites have this setting.")
                return connection
    raise ServiceValidationError("The satellite is not connected.")


find_connection = find_satellite


async def _set_wifi(call: ServiceCall) -> None:
    connection = find_connection(call.hass, call.data["device_id"])
    await async_set_wifi(connection, call.data["ssid"], call.data["password"])


SERVICE_SET_SETTING = "set_setting"
SET_SETTING_SCHEMA = vol.Schema({
    vol.Optional("device_id"): cv.string,
    vol.Required("key"): cv.string,
    vol.Required("value"): vol.Any(bool, int, float, str),
})


def _refuse(key: str, err: SettingError) -> ServiceValidationError:
    return ServiceValidationError(f"{key}: {err.code}")


async def _set_setting(call: ServiceCall) -> None:
    """Change a setting that lives in a form (general or per satellite), validated like the form."""
    hass = call.hass
    key, value, device_id = call.data["key"], call.data["value"], call.data.get("device_id")
    if key in GENERAL_KEYS:
        if device_id:
            raise ServiceValidationError("General settings apply to every satellite; leave the device empty.")
        try:
            value = validate_general(key, value)
        except SettingError as err:
            raise _refuse(key, err) from err
        entries = hass.config_entries.async_entries(DOMAIN)
        if not entries:
            raise ServiceValidationError("F.R.I.D.A.Y. is not set up.")
        entry = entries[0]
        general = dict(entry.options.get(OPTIONS_GENERAL) or {})
        old = general.get(key, "unset")
        general[key] = value
        hass.config_entries.async_update_entry(entry, options={**entry.options, OPTIONS_GENERAL: general})
        if old != value:
            log_settings_change(hass, None, SCOPE_GENERAL, {key: (old, value)})
        await async_apply_general_all(hass, entry)
        return
    if not device_id:
        raise ServiceValidationError(f"{key}: choose the satellite device.")
    connection = find_satellite(hass, device_id)
    if not connection.connected:
        raise ServiceValidationError("The satellite is not connected.")
    caps = connection.hello.capabilities
    try:
        value = validate_advanced(caps, key, value)
    except SettingError as err:
        raise _refuse(key, err) from err
    state = connection.state
    if key.startswith("wakeword_") and key.endswith("_mode"):
        modes = {d.key: state.get(d.key) for d in caps if d.key.startswith("wakeword_") and d.key.endswith("_mode")}
        modes[key] = value
        try:
            check_wakewords(modes)
        except SettingError as err:
            raise _refuse(key, err) from err
    if key == "ducking_level" and not state.get("ducking"):
        raise ServiceValidationError("Turn ducking on first.")
    try:
        await connection.send("set", key=key, value=value)
    except HomeAssistantError as err:
        raise ServiceValidationError(f"The satellite refused the change ({err}).") from err
    log_settings_change(hass, connection, SCOPE_ADVANCED, {key: (state.get(key), value)})


SERVICE_RETRY = "retry_provisioning"
RETRY_SCHEMA = vol.Schema({vol.Required("device_id"): cv.string})


async def _retry_provisioning(call: ServiceCall) -> None:
    """Redo the provisioning steps that failed for the satellite behind the device."""
    from . import orchestrator_for

    hass = call.hass
    connection = find_connection(hass, call.data["device_id"])
    sat_id = connection.hello.device["id"]
    for entry in hass.config_entries.async_entries(DOMAIN):
        for subentry_id, sub in entry.subentries.items():
            if sub.unique_id == sat_id and "provisioned" in sub.data:  # the legacy satellite has no such key
                hass.async_create_background_task(
                    orchestrator_for(hass, subentry_id, sat_id).run(retry=True), f"friday retry {sat_id}")
                return
    raise ServiceValidationError("This satellite is not managed by the provisioning flow.")


def async_register_services(hass: HomeAssistant) -> None:
    if not hass.services.has_service(DOMAIN, SERVICE_SET_WIFI):
        hass.services.async_register(DOMAIN, SERVICE_SET_WIFI, _set_wifi, schema=SCHEMA)
    if not hass.services.has_service(DOMAIN, SERVICE_SET_SETTING):
        hass.services.async_register(DOMAIN, SERVICE_SET_SETTING, _set_setting, schema=SET_SETTING_SCHEMA)
    if not hass.services.has_service(DOMAIN, SERVICE_RETRY):
        hass.services.async_register(DOMAIN, SERVICE_RETRY, _retry_provisioning, schema=RETRY_SCHEMA)
