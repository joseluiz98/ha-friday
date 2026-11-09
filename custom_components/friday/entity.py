"""Base entity and platform setup helper shared by all platforms."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import area_registry as ar, entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity, EntityCategory
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import slugify

from .connection import FridayConnection
from .const import DOMAIN
from .protocol import Descriptor

_CATEGORIES = {"config": EntityCategory.CONFIG, "diagnostic": EntityCategory.DIAGNOSTIC}
# 06/11: a satellite's settings (turn sounds, ducking, wake word modes...) are not part of the room it sits in.
# Assist targets an entity by its own area first and the device's only as a fallback, so "turn off the office"
# switched the turn sounds off. Their own area keeps them reachable by name and out of room-wide commands.
SETTINGS_AREA = "Ajustes do Mark"


def place_in_settings_area(hass: HomeAssistant, entity_id: str) -> None:
    """Moves a satellite setting to SETTINGS_AREA (created without a floor). An area the user chose is kept."""
    reg = er.async_get(hass)
    ent = reg.async_get(entity_id)
    if ent is None or ent.area_id is not None:
        return
    areas = ar.async_get(hass)
    area = areas.async_get_area_by_name(SETTINGS_AREA) or areas.async_create(SETTINGS_AREA, icon="mdi:tune-variant")
    reg.async_update_entity(entity_id, area_id=area.id)


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
        self.entity_id = (
            f"{descriptor.platform}.friday_{slugify(device['id'])}_{slugify(descriptor.key)}"
        )
        self._attr_entity_category = _CATEGORIES.get(descriptor.category)
        self._attr_entity_registry_enabled_default = descriptor.enabled_default is not False
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
        if (self._attr_entity_category == EntityCategory.CONFIG
                and self._connection.hello.device.get("kind") == "satellite"):
            place_in_settings_area(self.hass, self.entity_id)
        self.async_on_remove(self._connection.add_listener(self._on_changes))
        self.async_on_remove(
            self._connection.add_availability_listener(self._on_availability)
        )

    @callback
    def _on_changes(self, changes: dict, origin: str) -> None:
        if self.descriptor.key in changes or f"{self.descriptor.key}_attributes" in changes:
            self.async_write_ha_state()

    @property
    def extra_state_attributes(self):
        """Protocol v1: state key `<key>_attributes` (a dict) becomes the entity's attributes."""
        attrs = self._connection.state.get(f"{self.descriptor.key}_attributes")
        return attrs if isinstance(attrs, dict) else None

    @callback
    def _on_availability(self, _up: bool) -> None:
        self.async_write_ha_state()


def setup_platform(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
    platform: str,
    factory: Callable[[FridayConnection, Descriptor], FridayEntity],
    skip_keys: frozenset[str] = frozenset(),
    extra: Callable[[FridayConnection, dict], list[FridayEntity]] | None = None,
) -> None:
    """Create the platform's entities for each subentry once its hello arrives.

    A device that is down never blocks the others: its entities simply appear
    when it first connects. Hidden descriptors are protocol-only commands.
    """
    # One flag per subentry: a single shared `nonlocal` flag let the first
    # device's hello block the entities of every other device.
    added: set[str] = set()
    for subentry_id, connection in entry.runtime_data.items():

        def _try_add(
            _up: bool = True,
            connection: FridayConnection = connection,
            subentry_id: str = subentry_id,
        ) -> None:
            if subentry_id in added or connection.hello is None:
                return
            added.add(subentry_id)
            entities = [
                factory(connection, d)
                for d in connection.hello.capabilities
                if d.platform == platform and not d.hidden and d.key not in skip_keys
            ]
            if extra is not None:
                entities += extra(connection, dict(entry.subentries[subentry_id].data))
            if entities:
                async_add_entities(entities, config_subentry_id=subentry_id)

        entry.async_on_unload(connection.add_availability_listener(_try_add))
        _try_add()
