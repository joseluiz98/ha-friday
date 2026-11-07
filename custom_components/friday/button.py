"""Buttons: descriptor buttons plus the integration-made Reboot VM button."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.util import slugify

from . import provisioning
from .const import DEVICE_KIND_SATELLITE, DOMAIN
from .entity import FridayEntity, setup_platform
from .proxmox import log_action, proxmox_configured, reboot_vm

RESTART_FRIDAY = "restart_friday"
RESTART_PROGRAM = "restart_program"  # hidden satellite button


async def async_setup_entry(hass, entry, async_add_entities):
    setup_platform(
        hass, entry, async_add_entities, "button",
        lambda connection, descriptor: FridayButton(connection, descriptor, entry),
    )
    for subentry_id, subentry in entry.subentries.items():
        if subentry.subentry_type == "server" and proxmox_configured(dict(subentry.data)):
            async_add_entities(
                [RebootVmButton(entry, dict(subentry.data), subentry_id)],
                config_subentry_id=subentry_id,
            )


async def restart_satellites(entry: ConfigEntry) -> list[str]:
    """Press the hidden restart_program on every connected satellite; returns failures."""
    failed = []
    for connection in entry.runtime_data.values():
        hello = connection.hello
        if (not connection.connected or hello is None
                or hello.device.get("kind") != DEVICE_KIND_SATELLITE):
            continue
        try:
            await connection.send("press", key=RESTART_PROGRAM)
        except HomeAssistantError:
            failed.append(str(hello.device.get("id")))
    return failed


class FridayButton(FridayEntity, ButtonEntity):
    def __init__(self, connection, descriptor, entry=None):
        super().__init__(connection, descriptor)
        self._entry = entry
        self._attr_device_class = descriptor.device_class

    async def async_press(self) -> None:
        try:
            res = await self._connection.send("press", key=self.descriptor.key)
        except HomeAssistantError as err:
            log_action(self.hass, self.name or self.descriptor.key,
                       f"{self.descriptor.name} failed: {err}", self.entity_id)
            raise
        log_action(self.hass, self.descriptor.name, f"{self.descriptor.name} pressed", self.entity_id)
        if res.get("forgot_home") and self._entry is not None:
            # in a task: removing the subentry unloads this very entity
            self.hass.async_create_task(provisioning.forget_satellite(
                self.hass, self._entry, self._connection.subentry_id, self._connection.hello.device["id"]))
        if self.descriptor.key == RESTART_FRIDAY and self._entry is not None:
            failed = await restart_satellites(self._entry)
            if failed:
                log_action(self.hass, self.descriptor.name,
                           f"Satellite restart failed for: {', '.join(failed)}", self.entity_id)


class RebootVmButton(ButtonEntity):
    """Reboots the VM through the Proxmox API; works with the VM hung."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_name = "Reboot VM"
    _attr_device_class = "restart"
    _attr_icon = "mdi:server-network"

    def __init__(self, entry, cfg, subentry_id):
        self._entry = entry
        self._cfg = cfg
        self._subentry_id = subentry_id
        self._attr_unique_id = f"{subentry_id}_reboot_vm"
        self.entity_id = f"button.friday_{slugify(self._device_id)}_reboot_vm"

    @property
    def _device_id(self) -> str:
        conn = self._entry.runtime_data.get(self._subentry_id)
        hello = getattr(conn, "hello", None)
        return str(hello.device["id"]) if hello else "server"

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(identifiers={(DOMAIN, self._device_id)}, manufacturer="F.R.I.D.A.Y.")

    async def async_press(self) -> None:
        conn = self._entry.runtime_data.get(self._subentry_id)
        try:
            await reboot_vm(self.hass, self._cfg, conn)
        except HomeAssistantError as err:
            log_action(self.hass, "Reboot VM", f"Reboot VM failed: {err}", self.entity_id)
            raise
        log_action(self.hass, "Reboot VM", "Reboot VM requested", self.entity_id)
