"""friday.set_wifi: route a Wi-Fi change to the satellite behind a Home Assistant device."""

from __future__ import annotations

import re

import voluptuous as vol

from homeassistant.components.logbook import async_log_entry
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv, device_registry as dr

from .const import DEVICE_KIND_SATELLITE, DEVICE_KIND_SERVER, DOMAIN, OPTIONS_GENERAL
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


SERVICE_BUILD_IMAGE = "build_image"
BUILD_IMAGE_SCHEMA = vol.Schema({vol.Optional("version"): cv.string})
VERSION = re.compile(r"^[vV]?(\d+\.\d+\.\d+)$")
IMAGE_ERRORS = {
    "no_stable_release": "There is no stable release to build.",
    "package_missing": "The server has no OTA package for this version (unknown release, or not downloaded yet).",
    "server_busy": "The server is installing an update; try again when it finishes.",
    "busy": "An image build is already running.",
    "tag": "Invalid version.",
    "unavailable": "The server cannot build images.",
}


def normalize_version(raw: str | None) -> str | None:
    """'0.3.8' or 'v0.3.8' -> 'v0.3.8'; empty -> None (the stable release); anything else is refused."""
    text = (raw or "").strip()
    if not text:
        return None
    m = VERSION.match(text)
    if m is None:
        raise ServiceValidationError(f"Invalid version '{text}': use 0.3.8 or v0.3.8.")
    return "v" + m.group(1)


def find_server(hass: HomeAssistant):
    """The connected server agent, or a ServiceValidationError."""
    for entry in hass.config_entries.async_entries(DOMAIN):
        for connection in getattr(entry, "runtime_data", {}).values():
            hello = connection.hello
            if hello is not None and hello.device.get("kind") == DEVICE_KIND_SERVER:
                if not connection.connected:
                    break
                return connection
    raise ServiceValidationError("The F.R.I.D.A.Y. server is not connected.")


async def _build_image(call: ServiceCall) -> None:
    """Build the satellite provisioning image: the stable release by default, or the version given."""
    tag = normalize_version(call.data.get("version"))
    connection = find_server(call.hass)
    try:
        if tag is None:  # exactly what the button always did (the agent also refreshes the image sensor)
            await connection.send("press", key="image_build")
        else:
            await connection.send("image_build", tag=tag)
    except HomeAssistantError as err:
        code = str(err)
        raise ServiceValidationError(IMAGE_ERRORS.get(code, f"The server refused the build ({code}).")) from err


SERVICE_ANNOUNCE = "announce"
MESSAGE_MAX = 500
ANNOUNCE_SCHEMA = vol.Schema({
    vol.Required("message"): vol.All(cv.string, vol.Strip, vol.Length(min=1, max=MESSAGE_MAX)),
    vol.Optional("device_id"): vol.All(cv.ensure_list, [cv.string]),
})


def _device_name(hass: HomeAssistant, satellite_id: str) -> str:
    device = dr.async_get(hass).async_get_device(identifiers={(DOMAIN, satellite_id)})
    return (device.name_by_user or device.name) if device and (device.name_by_user or device.name) else satellite_id


async def _announce(call: ServiceCall) -> dict:
    """Speak the message on the chosen satellites (all by default); the answer is the bot's, per satellite."""
    hass = call.hass
    ids = [find_satellite(hass, dev).hello.device["id"] for dev in call.data.get("device_id") or []]
    connection = find_server(hass)
    try:
        data = await connection.send("announce", timeout=20, text=call.data["message"], satellites=ids or None)
    except HomeAssistantError as err:
        raise ServiceValidationError(f"The announcement failed ({err}).") from err
    if data.get("ok") is False:  # defensive: the agent core turns ok:false into an exception already
        raise ServiceValidationError(f"The announcement failed ({data.get('error', 'error')}).")
    answers = data.get("satelites") or {}
    targets = ids or list(answers)
    names = ", ".join(_device_name(hass, sid) for sid in targets) or "no satellite"
    detail = ", ".join(f"{_device_name(hass, sid)} {answer}" for sid, answer in answers.items())
    async_log_entry(hass, "F.R.I.D.A.Y.", f"Announcement sent to {names}" + (f": {detail}" if detail else ""), DOMAIN)
    return data


def async_register_services(hass: HomeAssistant) -> None:
    if not hass.services.has_service(DOMAIN, SERVICE_SET_WIFI):
        hass.services.async_register(DOMAIN, SERVICE_SET_WIFI, _set_wifi, schema=SCHEMA)
    if not hass.services.has_service(DOMAIN, SERVICE_SET_SETTING):
        hass.services.async_register(DOMAIN, SERVICE_SET_SETTING, _set_setting, schema=SET_SETTING_SCHEMA)
    if not hass.services.has_service(DOMAIN, SERVICE_RETRY):
        hass.services.async_register(DOMAIN, SERVICE_RETRY, _retry_provisioning, schema=RETRY_SCHEMA)
    if not hass.services.has_service(DOMAIN, SERVICE_BUILD_IMAGE):
        hass.services.async_register(DOMAIN, SERVICE_BUILD_IMAGE, _build_image, schema=BUILD_IMAGE_SCHEMA)
    if not hass.services.has_service(DOMAIN, SERVICE_ANNOUNCE):
        hass.services.async_register(
            DOMAIN, SERVICE_ANNOUNCE, _announce, schema=ANNOUNCE_SCHEMA, supports_response=SupportsResponse.OPTIONAL)
