"""Base entity and platform setup helper shared by all platforms."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity, EntityCategory
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .connection import FridayConnection
from .const import DOMAIN
from .protocol import Descriptor

_CATEGORIES = {"config": EntityCategory.CONFIG, "diagnostic": EntityCategory.DIAGNOSTIC}


class FridayEntity(Entity):
    """One descriptor of one agent. State comes only from the agent's pushes."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, connection: FridayConnection, descriptor: Descriptor) -> None:
        self._connection = connection
        self.descriptor = descriptor
        device = connection.hello.device
        self._attr_unique_id = f"{device['id']}_{descriptor.key}"
        self._attr_name = descriptor.name
        self._attr_entity_category = _CATEGORIES.get(descriptor.category)
        if descriptor.icon:
            self._attr_icon = descriptor.icon
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device["id"])},
            name=device.get("name"),
            model=device.get("model"),
            sw_version=device.get("sw_version"),
            hw_version=device.get("hw_version"),
            manufacturer="F.R.I.D.A.Y.",
        )

    @property
    def available(self) -> bool:
        return self._connection.connected and self.descriptor.available

    @property
    def _value(self) -> Any:
        return self._connection.state.get(self.descriptor.key)

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self._connection.add_listener(self._on_changes))
        self.async_on_remove(
            self._connection.add_availability_listener(self._on_availability)
        )

    @callback
    def _on_changes(self, changes: dict, origin: str) -> None:
        if self.descriptor.key in changes:
            self.async_write_ha_state()

    @callback
    def _on_availability(self, _up: bool) -> None:
        self.async_write_ha_state()


def setup_platform(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
    platform: str,
    factory: Callable[[FridayConnection, Descriptor], FridayEntity],
) -> None:
    """Create the platform's entities for each subentry once its hello arrives.

    A device that is down never blocks the others: its entities simply appear
    when it first connects. Hidden descriptors are protocol-only commands.
    """
    for subentry_id, connection in entry.runtime_data.items():
        added = False

        def _try_add(
            _up: bool = True,
            connection: FridayConnection = connection,
            subentry_id: str = subentry_id,
        ) -> None:
            nonlocal added
            if added or connection.hello is None:
                return
            added = True
            entities = [
                factory(connection, d)
                for d in connection.hello.capabilities
                if d.platform == platform and not d.hidden
            ]
            if entities:
                async_add_entities(entities, config_subentry_id=subentry_id)

        entry.async_on_unload(connection.add_availability_listener(_try_add))
        _try_add()
