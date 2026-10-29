"""Numbers from descriptors; writes go to the agent, state comes back by push."""

from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode

from .entity import FridayEntity, setup_platform


async def async_setup_entry(hass, entry, async_add_entities):
    setup_platform(hass, entry, async_add_entities, "number", FridayNumber)


class FridayNumber(FridayEntity, NumberEntity):
    def __init__(self, connection, descriptor):
        super().__init__(connection, descriptor)
        self._attr_native_unit_of_measurement = descriptor.unit
        self._attr_device_class = descriptor.device_class
        if descriptor.min is not None:
            self._attr_native_min_value = descriptor.min
        if descriptor.max is not None:
            self._attr_native_max_value = descriptor.max
        if descriptor.step is not None:
            self._attr_native_step = descriptor.step
        if descriptor.mode:
            self._attr_mode = NumberMode(descriptor.mode)

    @property
    def native_value(self):
        return self._value

    async def async_set_native_value(self, value: float) -> None:
        await self._connection.send("set", key=self.descriptor.key, value=value)
