"""Room temperature and humidity of a satellite: always two sensors, each with its own source.

The source is `satellite` (the agent's `room_*` descriptor) or `ha_entity` (mirrors another
sensor). Unset means unavailable until it is configured. The agent's temperature and humidity
descriptors are consumed here instead of becoming entities of their own.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN, UnitOfTemperature, PERCENTAGE
from homeassistant.core import callback
from homeassistant.helpers.event import async_track_state_change_event

from .const import DEVICE_KIND_SATELLITE, SOURCE_HA_ENTITY, SOURCE_SATELLITE
from .entity import FridayEntity
from .protocol import Descriptor

# quantity -> (descriptor key, name, unit, device class)
QUANTITIES = {
    "temperature": ("room_temperature", "Room temperature", UnitOfTemperature.CELSIUS, "temperature"),
    "humidity": ("room_humidity", "Room humidity", PERCENTAGE, "humidity"),
}
CONSUMED_KEYS = frozenset(k for k, *_ in QUANTITIES.values())


def source_key(key: str) -> str:
    return f"{key}_source"


def entity_key(key: str) -> str:
    return f"{key}_entity"


def declared(connection, key: str) -> bool:
    """Whether the agent announced this room descriptor."""
    return connection.hello is not None and any(d.key == key for d in connection.hello.capabilities)


def create_entities(connection, subentry_data: dict[str, Any]) -> list[FridayEntity]:
    if connection.hello is None or connection.hello.device.get("kind") != DEVICE_KIND_SATELLITE:
        return []
    return [RoomClimateSensor(connection, key, name, unit, device_class, subentry_data)
            for key, name, unit, device_class in QUANTITIES.values()]


class RoomClimateSensor(FridayEntity, SensorEntity):
    def __init__(self, connection, key, name, unit, device_class, data) -> None:
        super().__init__(connection, Descriptor(
            key=key, platform="sensor", name=name, unit=unit, device_class=device_class,
            state_class="measurement"))
        self._attr_native_unit_of_measurement = unit
        self._attr_device_class = device_class
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._source = data.get(source_key(key))
        self._source_entity = data.get(entity_key(key)) if self._source == SOURCE_HA_ENTITY else None

    @property
    def _mirrored(self):
        if not self._source_entity:
            return None
        state = self.hass.states.get(self._source_entity)
        if state is None or state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            return None
        return state

    @property
    def available(self) -> bool:
        if self._source == SOURCE_SATELLITE:
            return self._connection.connected and declared(self._connection, self.descriptor.key)
        if self._source == SOURCE_HA_ENTITY:
            return self.native_value is not None
        return False

    @property
    def native_value(self):
        raw = self._connection.state.get(self.descriptor.key) if self._source == SOURCE_SATELLITE else (
            self._mirrored.state if self._mirrored is not None else None)
        try:
            return None if raw is None else float(raw)
        except (TypeError, ValueError):
            return None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if self._source_entity:
            @callback
            def _changed(_event) -> None:
                self.async_write_ha_state()

            self.async_on_remove(
                async_track_state_change_event(self.hass, [self._source_entity], _changed))
