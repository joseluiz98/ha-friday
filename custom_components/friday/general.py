"""General settings: one value for every satellite, kept in the entry options and applied to each satellite."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.logbook import async_log_entry
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import slugify

from .const import DEVICE_KIND_SATELLITE, DOMAIN, OPTIONS_GENERAL
from .settings import GENERAL_KEYS, general_plan

_LOGGER = logging.getLogger(__name__)
SCOPE_GENERAL = "General settings"
SCOPE_ADVANCED = "Advanced settings"


def entity_id_for(connection, descriptor) -> str:
    device_id = connection.hello.device["id"]
    return f"{descriptor.platform}.friday_{slugify(device_id)}_{slugify(descriptor.key)}"


def _log(hass: HomeAssistant, connection, message: str) -> None:
    """One logbook line on the device's first entity (no entity for the integration as a whole)."""
    name, entity_id = "F.R.I.D.A.Y.", None
    if connection is not None and connection.hello is not None:
        name = connection.hello.device.get("name") or name
        first = next((d for d in connection.hello.capabilities if not d.hidden), None)
        entity_id = entity_id_for(connection, first) if first else None
    async_log_entry(hass, name, message, DOMAIN, entity_id)


def log_settings_change(hass: HomeAssistant, connection, scope: str, changes: dict[str, tuple]) -> None:
    """Form values keep no entity history: every change goes to the logbook as `key old → new`."""
    if not changes:
        return
    _log(hass, connection, f"{scope}: " + ", ".join(f"{k} {old} → {new}" for k, (old, new) in changes.items()))


async def async_apply_general(hass: HomeAssistant, entry: ConfigEntry, connection) -> dict[str, str]:
    """Send the general settings a satellite does not have yet; {key: "applied" | error}."""
    hello = connection.hello
    if not connection.connected or hello is None or hello.device.get("kind") != DEVICE_KIND_SATELLITE:
        return {}
    general = entry.options.get(OPTIONS_GENERAL) or {}
    sat_keys = {d.key for d in hello.capabilities}
    logged: set[str] = connection.__dict__.setdefault("general_unsupported_logged", set())
    for key in GENERAL_KEYS:
        if key in general and key not in sat_keys and key not in logged:
            logged.add(key)
            _log(hass, connection, f"{SCOPE_GENERAL}: {key} not supported on this satellite")
    result: dict[str, str] = {}
    for key, value in general_plan(general, dict(connection.state), sat_keys).items():
        try:
            await connection.send("set", key=key, value=value)
            result[key] = "applied"
        except HomeAssistantError as err:
            result[key] = str(err)
            _log(hass, connection, f"{SCOPE_GENERAL}: {key} not applied ({err})")
    return result


async def async_apply_general_all(hass: HomeAssistant, entry: ConfigEntry) -> None:
    for connection in list(getattr(entry, "runtime_data", {}).values()):
        await async_apply_general(hass, entry, connection)


def general_listeners(hass: HomeAssistant, entry: ConfigEntry, connection) -> tuple[Any, Any]:
    """(on availability, on state change) callbacks that keep one satellite on the general settings."""

    def _start(name: str) -> None:
        hass.async_create_background_task(
            async_apply_general(hass, entry, connection), f"friday general settings {name}")

    def _on_up(up: bool) -> None:
        if up:
            connection.__dict__.pop("general_unsupported_logged", None)
            _start("connect")

    def _on_changes(changes: dict, origin: str) -> None:
        if any(key in changes for key in GENERAL_KEYS):
            _start("state")

    return _on_up, _on_changes
