"""Brain reminders on each satellite: the next one (sensor) and the one that just rang (event).

The server agent publishes the hidden `reminders` state (upcoming reminders that ring at home, sorted). A reminder
that leaves the list at or after its time has rung: the event fires on every satellite, since reminders go to
whichever satellite Alex is near.
"""

from __future__ import annotations

from homeassistant.components.event import EventEntity
from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.core import callback
from homeassistant.util import dt as dt_util

from .const import DEVICE_KIND_SATELLITE, DEVICE_KIND_SERVER
from .entity import FridayEntity
from .protocol import Descriptor

KEY = "reminders"
RUNG_GRACE_S = 120  # a reminder that disappears up to this long before its time still counts as rung


def _server(entry):
    return next((c for c in entry.runtime_data.values()
                 if c.hello is not None and c.hello.device.get("kind") == DEVICE_KIND_SERVER), None)


def _items(entry) -> list[dict]:
    server = _server(entry)
    value = server.state.get(KEY) if server is not None and server.connected else None
    return [r for r in value if isinstance(r, dict) and r.get("at")] if isinstance(value, list) else []


def rung(before: list[dict], after: list[dict], now) -> list[dict]:
    """Reminders that left the list (or moved to a later time) at or just before their time."""
    still = {(r.get("name"), r.get("at")) for r in after}
    out = []
    for r in before:
        at = dt_util.parse_datetime(str(r.get("at")))
        if (r.get("name"), r.get("at")) not in still and at is not None \
                and (at - now).total_seconds() <= RUNG_GRACE_S:
            out.append(r)
    return out


class _ServerFed(FridayEntity):
    """Entity of a satellite device whose value comes from the server connection."""

    def __init__(self, connection, descriptor, entry) -> None:
        super().__init__(connection, descriptor)
        self._entry = entry

    @property
    def available(self) -> bool:
        server = _server(self._entry)
        return server is not None and server.connected

    async def async_added_to_hass(self) -> None:
        for conn in self._entry.runtime_data.values():
            self.async_on_remove(conn.add_listener(self._on_server_changes))
            self.async_on_remove(conn.add_availability_listener(self._on_availability))

    @callback
    def _on_server_changes(self, changes: dict, origin: str) -> None:
        if KEY in changes:
            self.async_write_ha_state()


class NextReminderSensor(_ServerFed, SensorEntity):
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(self, connection, entry) -> None:
        super().__init__(connection, Descriptor(key="next_reminder", platform="sensor", name="Next reminder",
                                                device_class="timestamp", icon="mdi:bell-ring-outline"), entry)

    @property
    def native_value(self):
        items = _items(self._entry)
        return dt_util.parse_datetime(str(items[0]["at"])) if items else None

    @property
    def extra_state_attributes(self):
        items = _items(self._entry)
        return {"name": items[0].get("name") if items else None, "total_active": len(items),
                "upcoming": items}


class ReminderEvent(_ServerFed, EventEntity):
    _attr_event_types = ["rang"]

    def __init__(self, connection, entry) -> None:
        super().__init__(connection, Descriptor(key="reminder", platform="event", name="Reminder",
                                                icon="mdi:bell-check-outline"), entry)
        self._last: list[dict] = _items(entry)

    @callback
    def _on_server_changes(self, changes: dict, origin: str) -> None:
        if KEY not in changes:
            return
        now_items = _items(self._entry)
        for r in rung(self._last, now_items, dt_util.utcnow()):
            self._trigger_event("rang", {"name": r.get("name"), "at": r.get("at")})
        self._last = now_items
        self.async_write_ha_state()


def _is_satellite(connection) -> bool:
    return connection.hello is not None and connection.hello.device.get("kind") == DEVICE_KIND_SATELLITE


def sensors(entry):
    return lambda connection, _data: [NextReminderSensor(connection, entry)] if _is_satellite(connection) else []


def events(entry):
    return lambda connection, _data: [ReminderEvent(connection, entry)] if _is_satellite(connection) else []
