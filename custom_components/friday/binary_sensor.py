"""Binary sensors from descriptors."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorEntity

from .entity import FridayEntity, setup_platform


async def async_setup_entry(hass, entry, async_add_entities):
    setup_platform(hass, entry, async_add_entities, "binary_sensor", FridayBinarySensor)


class FridayBinarySensor(FridayEntity, BinarySensorEntity):
    def __init__(self, connection, descriptor):
        super().__init__(connection, descriptor)
        self._attr_device_class = descriptor.device_class

    @property
    def is_on(self):
        value = self._value
        return None if value is None else bool(value)
