"""Sensors from descriptors."""

from __future__ import annotations

from homeassistant.components.sensor import SensorEntity
from homeassistant.util import dt as dt_util

from . import reminders, room_climate
from .entity import FridayEntity, setup_platform


async def async_setup_entry(hass, entry, async_add_entities):
    setup_platform(
        hass, entry, async_add_entities, "sensor", FridaySensor,
        skip_keys=room_climate.CONSUMED_KEYS,
        extra=lambda conn, data: room_climate.create_entities(conn, data) + reminders.sensors(entry)(conn, data))


class FridaySensor(FridayEntity, SensorEntity):
    def __init__(self, connection, descriptor):
        super().__init__(connection, descriptor)
        self._attr_native_unit_of_measurement = descriptor.unit
        self._attr_device_class = descriptor.device_class
        self._attr_state_class = descriptor.state_class

    @property
    def native_value(self):
        value = self._value
        kind = self.descriptor.device_class
        if kind not in ("timestamp", "date") or value is None:
            return value
        try:
            if kind == "date":
                return dt_util.parse_date(str(value))
            parsed = dt_util.parse_datetime(str(value))
        except (ValueError, TypeError):
            return None
        if parsed is None:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt_util.UTC)
