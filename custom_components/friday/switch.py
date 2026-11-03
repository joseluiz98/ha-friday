"""Switches from descriptors."""

from __future__ import annotations

from homeassistant.components.switch import SwitchEntity

from .entity import FridayEntity, setup_platform


async def async_setup_entry(hass, entry, async_add_entities):
    setup_platform(hass, entry, async_add_entities, "switch", FridaySwitch)


class FridaySwitch(FridayEntity, SwitchEntity):
    def __init__(self, connection, descriptor):
        super().__init__(connection, descriptor)
        self._attr_device_class = descriptor.device_class

    @property
    def is_on(self):
        value = self._value
        return None if value is None else bool(value)

    async def async_turn_on(self, **kwargs) -> None:
        await self._connection.send("set", key=self.descriptor.key, value=True)

    async def async_turn_off(self, **kwargs) -> None:
        await self._connection.send("set", key=self.descriptor.key, value=False)
