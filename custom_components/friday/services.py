"""friday.set_wifi: route a Wi-Fi change to the satellite behind a Home Assistant device."""

from __future__ import annotations

import voluptuous as vol

from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv, device_registry as dr

from .const import DEVICE_KIND_SATELLITE, DOMAIN
from .wifi import async_set_wifi

SERVICE_SET_WIFI = "set_wifi"
SCHEMA = vol.Schema({
    vol.Required("device_id"): cv.string,
    vol.Required("ssid"): vol.All(cv.string, vol.Length(min=1, max=32)),
    vol.Required("password"): vol.All(cv.string, vol.Length(min=8, max=63)),
})


def find_connection(hass: HomeAssistant, device_id: str):
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
                    raise ServiceValidationError("Only satellites can change Wi-Fi.")
                return connection
    raise ServiceValidationError("The satellite is not connected.")


async def _set_wifi(call: ServiceCall) -> None:
    connection = find_connection(call.hass, call.data["device_id"])
    await async_set_wifi(connection, call.data["ssid"], call.data["password"])


def async_register_services(hass: HomeAssistant) -> None:
    if not hass.services.has_service(DOMAIN, SERVICE_SET_WIFI):
        hass.services.async_register(DOMAIN, SERVICE_SET_WIFI, _set_wifi, schema=SCHEMA)
