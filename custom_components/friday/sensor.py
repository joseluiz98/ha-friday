"""Sensors from descriptors."""

from __future__ import annotations

from homeassistant.components.sensor import SensorEntity

from .entity import FridayEntity, setup_platform


async def async_setup_entry(hass, entry, async_add_entities):
    setup_platform(hass, entry, async_add_entities, "sensor", FridaySensor)


class FridaySensor(FridayEntity, SensorEntity):
    def __init__(self, connection, descriptor):
        super().__init__(connection, descriptor)
        self._attr_native_unit_of_measurement = descriptor.unit
        self._attr_device_class = descriptor.device_class
        self._attr_state_class = descriptor.state_class

    @property
    def native_value(self):
        return self._value
