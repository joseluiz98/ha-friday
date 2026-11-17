"""The integration updates itself through HACS when the server's release channel says so.

The server agent publishes the state key `ota_integration_target` (a tag, or None). When it differs from the
installed version (`manifest.json`; older = the server rolled back), the update entity HACS made for this repository is asked to install it. The
Home Assistant restart that loads the new code is not done here: `binary_sensor.<server>_restart_pending` turns on and
the nightly automation restarts the Core.
"""

from __future__ import annotations

import logging

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_connect, async_dispatcher_send
from homeassistant.helpers.event import async_call_later
from homeassistant.loader import async_get_integration

from .const import DEVICE_KIND_SERVER, DOMAIN
from .entity import FridayEntity
from .image_view import version_key
from .protocol import Descriptor

__all__ = ["version_key", "maybe_self_update", "restart_pending", "RestartPendingSensor"]

_LOGGER = logging.getLogger(__name__)

HACS_REPO = "joseluiz98/ha-friday"
TARGET_KEY = "ota_integration_target"
SIGNAL_RESTART_PENDING = f"{DOMAIN}_restart_pending"


def restart_pending(hass: HomeAssistant) -> bool:
    return bool(hass.data.get(DOMAIN, {}).get("restart_pending"))


def _set_restart_pending(hass: HomeAssistant) -> None:
    hass.data.setdefault(DOMAIN, {})["restart_pending"] = True
    async_dispatcher_send(hass, SIGNAL_RESTART_PENDING)


def find_hacs_update_entity(hass: HomeAssistant) -> str | None:
    """The entity_id of the HACS update entity of this repository, found in the registry (never a fixed id: the user
    may have renamed it). HACS keys the entity by the repository's numeric id, so the repository is looked up in
    HACS itself when the unique_id does not carry the name."""
    # 17/11: HA 2026.11 replaced HACS by the Marketplace (platform "marketplace", unique_id = GitHub repository id)
    candidates = [e for e in er.async_get(hass).entities.values()
                  if e.platform in ("hacs", "marketplace") and e.domain == "update"]
    for e in candidates:
        if HACS_REPO in e.unique_id:
            return e.entity_id
    for e in candidates:  # the entity's release link names the repository on both platforms
        st = hass.states.get(e.entity_id)
        url = str(st.attributes.get("release_url") or "") if st else ""
        if f"github.com/{HACS_REPO}/" in url:
            return e.entity_id
    try:
        repo_id = hass.data["hacs"].repositories.get_by_full_name(HACS_REPO).data.id
    except (KeyError, AttributeError, TypeError):
        return None
    for e in candidates:
        if repo_id is not None and e.unique_id == str(repo_id):
            return e.entity_id
    return None


async def maybe_self_update(hass: HomeAssistant, target_tag: str | None, installed: str) -> str:
    """`none` (nothing to do, or HACS refused: may be tried again), `already` (the running version, what HACS already
    has, or this tag was already requested since the start), `no_hacs_entity` (not found or not loaded yet) or
    `installing`. A target OLDER than the installed version is installed too: the server rolled the channel back."""
    if not target_tag:
        return "none"
    try:
        if version_key(target_tag) == version_key(installed):
            return "already"
    except ValueError:
        return "none"
    tried: set[str] = hass.data.setdefault(DOMAIN, {}).setdefault("self_update_tried", set())
    if target_tag in tried:
        return "already"
    entity_id = find_hacs_update_entity(hass)
    st = hass.states.get(entity_id) if entity_id else None
    if st is None or st.state in ("unavailable", "unknown"):  # HACS still loading at start: tried again later
        _LOGGER.info("Integration target %s: HACS update entity for %s not ready", target_tag, HACS_REPO)
        return "no_hacs_entity"
    if st.attributes.get("installed_version") == target_tag:
        # HACS already put it on disk (a restart is what is missing, or the tag and manifest.json disagree): never
        # reinstall it, or a mismatched release would reinstall and restart every night
        tried.add(target_tag)
        return "already"
    tried.add(target_tag)  # before the await: two triggers in a row must not install twice
    try:
        await hass.services.async_call("update", "install", {"entity_id": entity_id, "version": target_tag},
                                       blocking=True)
    except Exception as err:  # noqa: BLE001 - HACS raises its own types; the next trigger may try again
        tried.discard(target_tag)
        _LOGGER.warning("HACS did not install %s: %s", target_tag, err)
        return "none"
    _LOGGER.info("Integration %s installed through HACS (was %s); restart pending", target_tag, installed)
    return "installing"


RETRY_S = 120
RETRIES = 15  # 30 min of HACS loading after a Home Assistant start


async def run(hass: HomeAssistant, connection, attempt: int = 0) -> str:
    """Read the target from the server connection and act on it; HACS not ready yet → try again in RETRY_S."""
    target = connection.state.get(TARGET_KEY)
    installed = str((await async_get_integration(hass, DOMAIN)).version)
    result = await maybe_self_update(hass, target if isinstance(target, str) else None, installed)
    if result == "installing":
        _set_restart_pending(hass)
    elif result in ("no_hacs_entity", "none") and isinstance(target, str) and attempt < RETRIES:
        async def _again(_now) -> None:
            await run(hass, connection, attempt + 1)
        async_call_later(hass, RETRY_S, _again)
    return result


def watch(hass: HomeAssistant, connection):
    """Listeners (state, availability) of the server connection that trigger the check; returns the two unsubscribe
    callbacks."""

    def _is_server() -> bool:
        return connection.hello is not None and connection.hello.device.get("kind") == DEVICE_KIND_SERVER

    def _schedule() -> None:
        hass.async_create_background_task(run(hass, connection), "friday self update")

    @callback
    def _on_state(changes: dict, origin: str) -> None:
        if TARGET_KEY in changes and _is_server():
            _schedule()

    @callback
    def _on_up(up: bool) -> None:
        if up and _is_server() and connection.state.get(TARGET_KEY):
            _schedule()

    return connection.add_listener(_on_state), connection.add_availability_listener(_on_up)


class RestartPendingSensor(FridayEntity, BinarySensorEntity):
    """On after HACS installed a new integration version, until Home Assistant restarts (the flag lives in memory)."""

    def __init__(self, connection) -> None:
        super().__init__(connection, Descriptor(key="restart_pending", platform="binary_sensor",
                                                name="Restart pending", category="diagnostic",
                                                icon="mdi:restart-alert"))

    @property
    def available(self) -> bool:
        return True  # a flag of this Home Assistant, not of the server: the nightly restart must see it

    @property
    def is_on(self) -> bool:
        return restart_pending(self.hass)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(async_dispatcher_connect(self.hass, SIGNAL_RESTART_PENDING, self.async_write_ha_state))


def server_entities(connection, _data) -> list[FridayEntity]:
    hello = connection.hello
    return [RestartPendingSensor(connection)] if hello and hello.device.get("kind") == DEVICE_KIND_SERVER else []
