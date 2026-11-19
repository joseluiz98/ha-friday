"""The F.R.I.D.A.Y. integration."""

from __future__ import annotations

import json
import logging
import re

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.components.logbook import async_log_entry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.util import slugify

from .connection import FridayConnection
from .general import entity_id_for, general_listeners
from . import image_view, provisioning, self_update
from .recordings import RecordingView
from .services import async_register_services
from .const import (
    CONF_AREA_ID, CONF_HOST, CONF_PORT, CONF_TOKEN, DEVICE_KIND_SATELLITE, DEVICE_KIND_SERVER, DOMAIN, EVENT_CHANGE,
    ORIGIN_HA, READ_ONLY_PLATFORMS,
)

PLATFORMS = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.EVENT,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.UPDATE,
]

_LOGGER = logging.getLogger(__name__)
SERVER_VOICE_KEY = "voice"
SATELLITE_VOICE_KEY = "voice_enabled"

type FridayConfigEntry = ConfigEntry[dict[str, FridayConnection]]


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Register the integration's services (once, whatever the entries)."""
    async_register_services(hass)
    hass.http.register_view(RecordingView())
    hass.http.register_view(image_view.FridayImageView())
    hass.http.register_view(image_view.FridayImageListView())
    await _async_register_card(hass)
    return True


CARD_URL = "/friday_frontend"


async def _async_register_card(hass: HomeAssistant) -> None:
    """Serve the activations card and load it in every dashboard."""
    from pathlib import Path

    from homeassistant.components.frontend import add_extra_js_url
    from homeassistant.components.http import StaticPathConfig
    from homeassistant.loader import async_get_integration

    version = (await async_get_integration(hass, DOMAIN)).version
    await hass.http.async_register_static_paths(
        [StaticPathConfig(CARD_URL, str(Path(__file__).parent / "frontend"), False)])
    if "frontend_extra_module_url" not in hass.data:
        return  # frontend not loaded (e.g. headless): the card is simply unavailable
    for card in ("friday-activations-card", "friday-ota-card"):
        add_extra_js_url(hass, f"{CARD_URL}/{card}.js?v={version}")


LEGACY_TITLE_PREFIX = "F.R.I.D.A.Y. "

# 04/11: entities whose descriptor key was renamed (old -> new); the registry entry is migrated so the entity keeps
# its history and the user's settings
RENAMED_KEYS = {"follow_up": "reply_wait"}


# 10/11: keys that left the device page (engineering settings: forms + friday.set_setting). Their old entities
# are removed from the registry on setup. Entity unique_id = "<device>_<key>".
RETIRED_KEYS = frozenset({
    "reply_wait", "follow_up_window", "no_follow_up_while_music", "no_follow_up_after_home_commands",
    "end_of_speech_pause", "continuous_speech_limit", "ducking_restore",
    "close_mic_awaiting_reply", "continue_listening_while_speaking",
    "ota_channel", "sw_version",  # 14/11: Advanced step / the update entity "Software" shows the version
    # 17/11: semantics or unit changed (spec 2026-11-17-diagnostic-sensors)
    "cpu_load", "memory_available", "memory_total", "vm_cpu_load_pct", "host_available_mb",
    # 18/11: the 04:15 reindex was retired (spec 2026-11-18-memory-watcher-health)
    "memory_index_last_success",
    # 18/11: each satellite reports its own upload (spec 2026-11-18-per-satellite-recordings)
    "wakeword_sync_last_ok",
})
_RETIRED_WAKEWORD = re.compile(r"^.+_wakeword_.+_(mode|sensitivity)$")


def _remove_retired_entities(hass: HomeAssistant, entry: FridayConfigEntry) -> None:
    reg = er.async_get(hass)
    for entity in list(er.async_entries_for_config_entry(reg, entry.entry_id)):
        if _RETIRED_WAKEWORD.match(entity.unique_id) or any(
                entity.unique_id.endswith(f"_{key}") for key in RETIRED_KEYS):
            reg.async_remove(entity.entity_id)


def _reload_fingerprint(entry: ConfigEntry) -> str:
    """What a reload depends on: entry data and the subentries. Options (general settings) are left out."""
    return json.dumps({
        "data": dict(entry.data),
        "subentries": {sid: [sub.subentry_id, sub.subentry_type, sub.title, dict(sub.data)]
                       for sid, sub in entry.subentries.items()},
    }, sort_keys=True, default=str)


def _migrate_renamed_keys(hass: HomeAssistant, entry: FridayConfigEntry) -> None:
    """Move registry entries from an old descriptor key to its new one. The default entity_id follows the key; an
    entity_id the user renamed is kept."""
    reg = er.async_get(hass)
    for entity in list(er.async_entries_for_config_entry(reg, entry.entry_id)):
        for old, new in RENAMED_KEYS.items():
            suffix = f"_{old}"
            if not entity.unique_id.endswith(suffix):
                continue
            device_id = entity.unique_id.removesuffix(suffix)
            new_unique_id = f"{device_id}_{new}"
            default_old = f"{entity.domain}.friday_{slugify(device_id)}_{slugify(old)}"
            kwargs = {"new_unique_id": new_unique_id}
            if entity.entity_id == default_old:
                new_entity_id = f"{entity.domain}.friday_{slugify(device_id)}_{slugify(new)}"
                if reg.async_get(new_entity_id) is None:
                    kwargs["new_entity_id"] = new_entity_id
            try:
                reg.async_update_entity(entity.entity_id, **kwargs)
            except ValueError:  # the new unique_id already exists: leave the old entry alone
                _LOGGER.warning("Could not migrate %s to %s", entity.entity_id, new_unique_id)
            break


async def async_setup_entry(hass: HomeAssistant, entry: FridayConfigEntry) -> bool:
    """Start one connection per subentry; entities appear once each hello arrives."""
    connections: dict[str, FridayConnection] = {}
    _migrate_renamed_keys(hass, entry)
    _remove_retired_entities(hass, entry)
    for subentry in list(entry.subentries.values()):
        # 31/10: titles no longer repeat the integration name ("F.R.I.D.A.Y. Server" -> "Server")
        if subentry.title.startswith(LEGACY_TITLE_PREFIX):
            hass.config_entries.async_update_subentry(
                entry, subentry, title=subentry.title.removeprefix(LEGACY_TITLE_PREFIX))
    for subentry_id, subentry in entry.subentries.items():
        data = subentry.data
        connections[subentry_id] = FridayConnection(
            hass, data[CONF_HOST], data[CONF_PORT], data[CONF_TOKEN], subentry_id
        )
    entry.runtime_data = connections

    # Platforms register their listeners first so no hello is missed.
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    for subentry_id, connection in connections.items():
        # A new capability list replaces the old one: rebuild the entities.
        def _reload() -> None:
            hass.config_entries.async_schedule_reload(entry.entry_id)

        entry.async_on_unload(connection.add_capabilities_listener(_reload))
        entry.async_on_unload(connection.add_listener(_change_listener(hass, connection)))
        entry.async_on_unload(connection.add_listener(_voice_propagation_listener(hass, entry, connection)))
        entry.async_on_unload(connection.add_availability_listener(_late_join_listener(hass, entry, connection)))
        entry.async_on_unload(connection.add_availability_listener(_server_join_listener(hass, entry, connection)))
        entry.async_on_unload(connection.add_availability_listener(_ota_credentials_listener(hass, entry, connection)))
        entry.async_on_unload(connection.add_availability_listener(_sync_host_key_listener(hass, entry, connection)))
        for unsub in self_update.watch(hass, connection):
            entry.async_on_unload(unsub)
        entry.async_on_unload(connection.add_log_listener(_log_listener(hass, connection)))
        on_up, on_changes = general_listeners(hass, entry, connection)
        entry.async_on_unload(connection.add_availability_listener(on_up))
        entry.async_on_unload(connection.add_listener(on_changes))
        sub = entry.subentries[subentry_id]
        if sub.data.get("provisioned") is False:
            _watch_new_satellite(hass, entry, subentry_id, sub, connection)
        area_id = entry.subentries[subentry_id].data.get(CONF_AREA_ID)
        if area_id:
            entry.async_on_unload(_area_listener(hass, entry, subentry_id, connection))
        await connection.start()

    # 18/11: the satellite's room (satellites.json, read by the voice) follows its device's area in HA
    entry.async_on_unload(hass.bus.async_listen(dr.EVENT_DEVICE_REGISTRY_UPDATED, provisioning.device_area_listener(hass)))
    provisioning.sync_registry_rooms(hass, entry)
    hass.data.setdefault(DOMAIN, {}).setdefault("fingerprints", {})[entry.entry_id] = _reload_fingerprint(entry)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


def orchestrator_for(hass: HomeAssistant, subentry_id: str, sat_id: str):
    """The provisioning orchestrator of a satellite; created once and kept across entry reloads."""
    store = hass.data.setdefault(DOMAIN, {}).setdefault("orchestrators", {})
    if sat_id not in store:
        store[sat_id] = provisioning.Orchestrator(hass, sat_id, subentry_id)
    return store[sat_id]


def _watch_new_satellite(hass, entry, subentry_id, subentry, connection) -> None:
    """A satellite adopted with the spoken code and not yet provisioned: run the orchestrator when it connects,
    and apply the area it picks in the presentation."""
    sat_id = subentry.unique_id
    if not sat_id:
        return
    orch = orchestrator_for(hass, subentry_id, sat_id)

    def _on_up(up: bool) -> None:
        if up:
            hass.async_create_background_task(orch.run(), f"friday provisioning {sat_id}")

    def _on_event(key: str, event_type: str, data: dict) -> None:
        if key == "onboarding" and event_type == "area_selected" and isinstance(data.get("area_id"), str):
            hass.async_create_background_task(orch.area_selected(data["area_id"]), f"friday area {sat_id}")

    def _on_state(changes: dict, origin: str) -> None:
        if changes.get("program_state") == "idle":
            hass.async_create_background_task(orch.conversation_closed(), f"friday ready {sat_id}")

    entry.async_on_unload(connection.add_availability_listener(_on_up))
    entry.async_on_unload(connection.add_event_listener(_on_event))
    entry.async_on_unload(connection.add_listener(_on_state))
    if connection.connected:
        _on_up(True)


_entity_id = entity_id_for


def _change_listener(hass, connection):
    """Fire friday_change for changes the satellite made itself (not HA's own)."""

    def _on_changes(changes: dict, origin: str) -> None:
        if origin == ORIGIN_HA or connection.hello is None:
            return
        for d in connection.hello.capabilities:
            if d.key not in changes or d.hidden or d.platform in READ_ONLY_PLATFORMS:
                continue
            hass.bus.async_fire(EVENT_CHANGE, {
                "device_id": connection.hello.device["id"],
                "entity_id": _entity_id(connection, d),
                "key": d.key, "value": changes[d.key], "origin": origin,
            })

    return _on_changes


def _voice_propagation_listener(hass, entry, connection):
    """The server's global voice switch, whatever changed it, is mirrored to every satellite."""

    def _on_changes(changes: dict, origin: str) -> None:
        hello = connection.hello
        if (SERVER_VOICE_KEY not in changes or hello is None
                or hello.device.get("kind") != DEVICE_KIND_SERVER):
            return
        value = bool(changes[SERVER_VOICE_KEY])

        async def _push() -> None:
            for other in list(entry.runtime_data.values()):
                h = other.hello
                if other is connection or not other.connected or h is None \
                        or h.device.get("kind") != DEVICE_KIND_SATELLITE:
                    continue
                try:
                    await other.send("set", key=SATELLITE_VOICE_KEY, value=value)
                except HomeAssistantError as err:
                    _LOGGER.warning("Voice switch not applied on satellite %s: %s",
                                    h.device.get("id"), err)

        hass.async_create_background_task(_push(), "friday voice propagation")

    return _on_changes


def _late_join_listener(hass, entry, connection):
    """A satellite that (re)connects while the server voice is known off is told to turn off."""

    def _on_up(up: bool) -> None:
        hello = connection.hello
        if not up or hello is None or hello.device.get("kind") != DEVICE_KIND_SATELLITE:
            return
        if not any(c is not connection and c.hello is not None
                   and c.hello.device.get("kind") == DEVICE_KIND_SERVER
                   and c.state.get(SERVER_VOICE_KEY) is False
                   for c in entry.runtime_data.values()):
            return

        async def _push() -> None:
            try:
                await connection.send("set", key=SATELLITE_VOICE_KEY, value=False)
            except HomeAssistantError as err:
                _LOGGER.warning("Voice switch not applied on satellite %s: %s",
                                hello.device.get("id"), err)

        hass.async_create_background_task(_push(), "friday voice late join")

    return _on_up


def _issue_missing_ota_credentials(hass, entry, sat_conn) -> None:
    """A satellite already provisioned (or legacy) whose agent reports no OTA credential gets one, silently: once per
    Home Assistant start (plan 2026-11-10 Task 16). Needs the server connected; tried again when the server joins."""
    hello = sat_conn.hello
    if (hello is None or hello.device.get("kind") != DEVICE_KIND_SATELLITE or not sat_conn.connected
            or sat_conn.state.get("ota_credentials") is not False):
        return
    sat_id = hello.device.get("id")
    subentry_id = next((sid for sid, c in entry.runtime_data.items() if c is sat_conn), None)
    sub = entry.subentries.get(subentry_id) if subentry_id else None
    if not sat_id or sub is None or sub.data.get("provisioned") is False:
        return  # a new satellite gets it from its own provisioning run
    if not any(c.connected and c.hello is not None and c.hello.device.get("kind") == DEVICE_KIND_SERVER
               for c in entry.runtime_data.values()):
        return
    tried = hass.data.setdefault(DOMAIN, {}).setdefault("ota_credentials_tried", set())
    if sat_id in tried:
        return
    tried.add(sat_id)

    async def _issue() -> None:
        try:
            await orchestrator_for(hass, subentry_id, sat_id).issue_ota_credentials()
            _LOGGER.info("Satellite %s got its OTA credential", sat_id)
        except (HomeAssistantError, provisioning.StepError) as err:
            _LOGGER.warning("OTA credential not delivered to satellite %s: %s", sat_id, err)

    hass.async_create_background_task(_issue(), f"friday ota credential {sat_id}")


def _ota_credentials_listener(hass, entry, connection):
    def _on_up(up: bool) -> None:
        if not up or connection.hello is None:
            return
        if connection.hello.device.get("kind") == DEVICE_KIND_SERVER:
            for other in list(entry.runtime_data.values()):
                _issue_missing_ota_credentials(hass, entry, other)
        else:
            _issue_missing_ota_credentials(hass, entry, connection)

    return _on_up


def _pin_missing_sync_host_key(hass, entry, sat_conn) -> None:
    """A satellite whose agent reports `sync_host_known: false` (paired before the key was delivered, or its upload
    address changed) gets the VM's SSH host key, without redoing any provisioning step (plan 2026-11-18 Task 3). An
    agent that does not report the key at all is too old to take it. Needs the server; tried again when it joins and
    on every reconnect until the agent reports the key as known."""
    hello = sat_conn.hello
    if (hello is None or hello.device.get("kind") != DEVICE_KIND_SATELLITE or not sat_conn.connected
            or sat_conn.state.get("sync_host_known") is not False):
        return
    sat_id = hello.device.get("id")
    subentry_id = next((sid for sid, c in entry.runtime_data.items() if c is sat_conn), None)
    if not sat_id or subentry_id is None:
        return
    if not any(c.connected and c.hello is not None and c.hello.device.get("kind") == DEVICE_KIND_SERVER
               for c in entry.runtime_data.values()):
        return
    busy = hass.data.setdefault(DOMAIN, {}).setdefault("sync_host_key_busy", set())
    if sat_id in busy:
        return
    busy.add(sat_id)

    async def _pin() -> None:
        try:
            if await orchestrator_for(hass, subentry_id, sat_id).pin_sync_host_key():
                _LOGGER.info("Satellite %s pinned the VM host key for its nightly upload", sat_id)
        except (HomeAssistantError, provisioning.StepError) as err:
            _LOGGER.warning("VM host key not delivered to satellite %s: %s", sat_id, err)
        finally:
            busy.discard(sat_id)

    hass.async_create_background_task(_pin(), f"friday sync host key {sat_id}")


def _sync_host_key_listener(hass, entry, connection):
    def _on_up(up: bool) -> None:
        if not up or connection.hello is None:
            return
        if connection.hello.device.get("kind") == DEVICE_KIND_SERVER:
            for other in list(entry.runtime_data.values()):
                _pin_missing_sync_host_key(hass, entry, other)
        else:
            _pin_missing_sync_host_key(hass, entry, connection)

    return _on_up


def _server_join_listener(hass, entry, connection):
    """The server (re)connects after the satellites and its voice is off: turn them off (never force on)."""

    def _on_up(up: bool) -> None:
        hello = connection.hello
        if (not up or hello is None or hello.device.get("kind") != DEVICE_KIND_SERVER
                or connection.state.get(SERVER_VOICE_KEY) is not False):
            return

        async def _push() -> None:
            for other in list(entry.runtime_data.values()):
                h = other.hello
                if other is connection or not other.connected or h is None \
                        or h.device.get("kind") != DEVICE_KIND_SATELLITE:
                    continue
                try:
                    await other.send("set", key=SATELLITE_VOICE_KEY, value=False)
                except HomeAssistantError as err:
                    _LOGGER.warning("Voice switch not applied on satellite %s: %s",
                                    h.device.get("id"), err)

        hass.async_create_background_task(_push(), "friday voice server join")

    return _on_up


def _log_listener(hass, connection):
    """Agent log messages become logbook entries on the device's first entity."""

    def _on_log(message: str, data: dict) -> None:
        if connection.hello is None:
            return
        first = next((d for d in connection.hello.capabilities if not d.hidden), None)
        async_log_entry(
            hass, connection.hello.device.get("name") or "F.R.I.D.A.Y.", message,
            DOMAIN, _entity_id(connection, first) if first else None,
        )

    return _on_log


def _area_listener(hass, entry, subentry_id, connection):
    """Give the satellite's device its area as soon as the agent has said who it is."""

    def _apply(_up: bool = True) -> None:
        if connection.hello is not None:
            apply_satellite_area(hass, entry, subentry_id, connection.hello.device["id"])

    _apply()
    return connection.add_availability_listener(_apply)


def apply_satellite_area(
    hass: HomeAssistant, entry: ConfigEntry, subentry_id: str, device_id: str
) -> None:
    """Set the subentry's area on its device, unless the user already placed it elsewhere. A subentry already removed
    (factory reset / "Forget") is skipped: raising here, inside the connection's last callback, failed the unload."""
    sub = entry.subentries.get(subentry_id)
    if sub is None:
        return
    area_id = sub.data.get(CONF_AREA_ID)
    if not area_id:
        return
    reg = dr.async_get(hass)
    device = reg.async_get_or_create(
        config_entry_id=entry.entry_id,
        config_subentry_id=subentry_id,
        identifiers={(DOMAIN, device_id)},
    )
    if device.area_id is None:
        reg.async_update_device(device.id, area_id=area_id)


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Subentry added, removed or edited: reload. Options-only changes (general settings) do not reload."""
    fingerprints = hass.data.setdefault(DOMAIN, {}).setdefault("fingerprints", {})
    if fingerprints.get(entry.entry_id) == _reload_fingerprint(entry):
        return
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: FridayConfigEntry) -> bool:
    """Stop the connections and unload the platforms."""
    ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if ok:
        for connection in entry.runtime_data.values():
            await connection.stop()
    return ok
