"""Reboot the F.R.I.D.A.Y. VM through the Proxmox API (works even when the VM is hung)."""

from __future__ import annotations

from typing import Any

import aiohttp

from homeassistant.components import logbook
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    CONF_PROXMOX_NODE, CONF_PROXMOX_TOKEN_ID, CONF_PROXMOX_TOKEN_SECRET,
    CONF_PROXMOX_URL, CONF_PROXMOX_VMID, PROXMOX_FIELDS,
)

TIMEOUT = 15


def proxmox_configured(cfg: dict[str, Any]) -> bool:
    return all(cfg.get(k) not in (None, "") for k in PROXMOX_FIELDS)


async def reboot_vm(hass: HomeAssistant, cfg: dict[str, Any], server_conn) -> None:
    """Refuse while a conversation is open (only knowable when the Server agent is connected),
    then ask Proxmox to reboot the VM. Never logs the token."""
    if server_conn is not None and server_conn.connected and server_conn.state.get(
        "conversation_open"
    ):
        raise HomeAssistantError("conversation_open")
    url = (
        f"{str(cfg[CONF_PROXMOX_URL]).rstrip('/')}/api2/json/nodes/{cfg[CONF_PROXMOX_NODE]}"
        f"/qemu/{cfg[CONF_PROXMOX_VMID]}/status/reboot"
    )
    auth = f"PVEAPIToken={cfg[CONF_PROXMOX_TOKEN_ID]}={cfg[CONF_PROXMOX_TOKEN_SECRET]}"
    session = async_get_clientsession(hass)
    try:
        async with session.post(
            url, headers={"Authorization": auth}, ssl=False,
            timeout=aiohttp.ClientTimeout(total=TIMEOUT),
        ) as resp:
            if resp.status >= 400:
                raise HomeAssistantError(f"proxmox_error_{resp.status}")
    except (aiohttp.ClientError, TimeoutError) as err:
        raise HomeAssistantError("proxmox_unreachable") from err


def log_action(hass: HomeAssistant, name: str, message: str, entity_id: str | None) -> None:
    """Logbook entry attached to a device entity."""
    logbook.async_log_entry(hass, name, message, "friday", entity_id)
