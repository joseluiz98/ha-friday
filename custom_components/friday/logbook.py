"""Describe friday_change events in the logbook (Activity)."""

from __future__ import annotations

from collections.abc import Callable

from homeassistant.components.logbook import LOGBOOK_ENTRY_ENTITY_ID, LOGBOOK_ENTRY_MESSAGE
from homeassistant.core import Event, HomeAssistant, callback

from .const import DOMAIN, EVENT_CHANGE

_BY = {"voice": "by voice", "button": "by button"}


@callback
def async_describe_events(
    hass: HomeAssistant,
    async_describe_event: Callable[[str, str, Callable[[Event], dict]], None],
) -> None:
    """Register the description of friday_change."""

    @callback
    def describe(event: Event) -> dict:
        data = event.data
        origin = data.get("origin")
        by = _BY.get(origin, "by the satellite")
        return {
            LOGBOOK_ENTRY_MESSAGE: f"set to {data.get('value')} {by}",
            LOGBOOK_ENTRY_ENTITY_ID: data.get("entity_id"),
        }

    async_describe_event(DOMAIN, EVENT_CHANGE, describe)
