"""Buttons from descriptors."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity

from .entity import FridayEntity, setup_platform


async def async_setup_entry(hass, entry, async_add_entities):
    setup_platform(hass, entry, async_add_entities, "button", FridayButton)


class FridayButton(FridayEntity, ButtonEntity):
    def __init__(self, connection, descriptor):
        super().__init__(connection, descriptor)
        self._attr_device_class = descriptor.device_class

    async def async_press(self) -> None:
        await self._connection.send("press", key=self.descriptor.key)
