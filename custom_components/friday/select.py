"""Selects from descriptors."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity

from .entity import FridayEntity, setup_platform


async def async_setup_entry(hass, entry, async_add_entities):
    setup_platform(hass, entry, async_add_entities, "select", FridaySelect)


class FridaySelect(FridayEntity, SelectEntity):
    def __init__(self, connection, descriptor):
        super().__init__(connection, descriptor)
        self._attr_options = list(descriptor.options or [])

    @property
    def current_option(self):
        return self._value

    async def async_select_option(self, option: str) -> None:
        await self._connection.send("set", key=self.descriptor.key, value=option)
