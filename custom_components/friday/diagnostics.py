"""Download diagnostics for a F.R.I.D.A.Y. device (integration side + the agent's own report)."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceEntry

from .const import DOMAIN

TO_REDACT = {"token", "password", "senha_porta_local", "proxmox_token_secret", "Authorization"}
_PARTS = ("secret", "token", "password", "psk", "senha")
REDACTED = "**REDACTED**"


def _redact(obj: Any) -> Any:
    """Exact keys via HA's helper, then any key containing a sensitive word, recursively."""
    if isinstance(obj, dict):
        obj = async_redact_data(obj, TO_REDACT)
        return {k: (REDACTED if any(p in str(k).lower() for p in _PARTS) else _redact(v))
                for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_redact(v) for v in obj]
    return obj


async def async_get_device_diagnostics(hass: HomeAssistant, entry: ConfigEntry, device: DeviceEntry) -> dict[str, Any]:
    device_id = next((i for d, i in device.identifiers if d == DOMAIN), None)
    connection = subentry = None
    for sub_id, conn in (getattr(entry, "runtime_data", None) or {}).items():
        if conn.hello is not None and conn.hello.device.get("id") == device_id:
            connection, subentry = conn, entry.subentries.get(sub_id)
            break
    out: dict[str, Any] = {
        "entry": {"title": entry.title, "subentry": dict(subentry.data) if subentry else None},
        "device": {"id": device_id, "name": device.name, "sw_version": device.sw_version},
    }
    if connection is None:
        out["agent"] = "unavailable"
        return _redact(out)
    out["connected"] = connection.connected
    out["hello_device"] = dict(connection.hello.device)
    out["capabilities"] = [c.key for c in connection.hello.capabilities]
    out["state"] = dict(connection.state)
    try:
        out["agent"] = await connection.send("diagnostics", timeout=30)
    except HomeAssistantError:
        out["agent"] = "unavailable"
    return _redact(out)
