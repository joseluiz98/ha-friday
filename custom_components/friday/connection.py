"""Websocket connection from Home Assistant to one F.R.I.D.A.Y. agent."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import logging
from typing import Any

import aiohttp

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import DOMAIN, WS_PATH
from .protocol import (
    PROTOCOL_VERSION,
    Hello,
    ProtocolError,
    command,
    parse_capabilities,
    parse_hello,
    parse_message,
)

_LOGGER = logging.getLogger(__name__)

BACKOFF_START = 1.0
BACKOFF_MAX = 60.0
HELLO_TIMEOUT = 10.0
HEARTBEAT = 30.0


def _remover(listeners: list, cb: Callable) -> Callable[[], None]:
    listeners.append(cb)

    def remove() -> None:
        if cb in listeners:
            listeners.remove(cb)

    return remove


class FridayConnection:
    """Keeps a websocket to an agent alive and dispatches its messages."""

    def __init__(
        self, hass: HomeAssistant, host: str, port: int, token: str, subentry_id: str
    ) -> None:
        self.hass = hass
        self._host = host
        self._port = port
        self._token = token
        self._subentry_id = subentry_id
        self.connected = False
        self._mismatch = False
        self.hello: Hello | None = None
        self.state: dict[str, Any] = {}
        self._task: asyncio.Task | None = None
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._next_id = 0
        self._pending: dict[int, asyncio.Future] = {}
        self._listeners: list[Callable[[dict, str], None]] = []
        self._event_listeners: list[Callable[[str, str, dict], None]] = []
        self._log_listeners: list[Callable[[str, dict], None]] = []
        self._availability_listeners: list[Callable[[bool], None]] = []
        self._capabilities_listeners: list[Callable[[], None]] = []

    def add_listener(self, cb):
        return _remover(self._listeners, cb)

    def add_event_listener(self, cb):
        return _remover(self._event_listeners, cb)

    def add_log_listener(self, cb):
        return _remover(self._log_listeners, cb)

    def add_availability_listener(self, cb):
        return _remover(self._availability_listeners, cb)

    def add_capabilities_listener(self, cb):
        return _remover(self._capabilities_listeners, cb)

    async def start(self) -> None:
        """Start connecting in the background."""
        if self._task is None:
            self._task = self.hass.async_create_background_task(
                self._run(), f"friday connection {self._subentry_id}"
            )

    async def stop(self) -> None:
        """Stop and close the connection."""
        task, self._task = self._task, None
        if task:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        if self._ws is not None and not self._ws.closed:
            await self._ws.close()
        self._ws = None
        self._set_connected(False)

    def _set_connected(self, value: bool) -> None:
        if self.connected == value:
            return
        self.connected = value
        if not value:
            for fut in self._pending.values():
                if not fut.done():
                    fut.set_exception(HomeAssistantError("not connected"))
            self._pending.clear()
        for cb in list(self._availability_listeners):
            cb(value)

    async def send(self, name: str, timeout: float = 10, **fields: Any) -> dict:
        """Send a command and wait for its result."""
        ws = self._ws
        if not self.connected or ws is None or ws.closed:
            raise HomeAssistantError("not connected")
        self._next_id += 1
        cmd_id = self._next_id
        fut: asyncio.Future = self.hass.loop.create_future()
        self._pending[cmd_id] = fut
        try:
            await ws.send_str(command(cmd_id, name, **fields))
            async with asyncio.timeout(timeout):
                return await fut
        except TimeoutError as err:
            raise HomeAssistantError("timeout") from err
        except (aiohttp.ClientError, ConnectionError) as err:
            raise HomeAssistantError("not connected") from err
        finally:
            self._pending.pop(cmd_id, None)

    async def _run(self) -> None:
        backoff = BACKOFF_START
        while True:
            try:
                await self._session()
                backoff = BACKOFF_START
            except asyncio.CancelledError:
                raise
            except (aiohttp.ClientError, OSError, TimeoutError, ProtocolError) as err:
                _LOGGER.debug("Agent %s:%s connection failed: %s", self._host, self._port, err)
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Unexpected error in agent connection")
            self._set_connected(False)
            self._ws = None
            if self._mismatch:
                self._mismatch = False
                backoff = BACKOFF_MAX * 5  # incompatible agent: retry rarely
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, BACKOFF_MAX)

    async def _session(self) -> None:
        session = async_get_clientsession(self.hass)
        url = f"ws://{self._host}:{self._port}{WS_PATH}"
        async with session.ws_connect(
            url, headers={"Authorization": f"Bearer {self._token}"},
            heartbeat=HEARTBEAT,
        ) as ws:
            self._ws = ws
            async with asyncio.timeout(HELLO_TIMEOUT):
                first = await ws.receive_str()
            msg = parse_message(first)
            proto = msg.get("protocol")
            if (
                msg.get("type") == "hello"
                and (isinstance(proto, bool) or not isinstance(proto, int)
                     or proto != PROTOCOL_VERSION)
            ):
                self._mismatch = True
                await ws.close()
                # aiohttp can re-arm its heartbeat timer after close() when
                # the last frame was just received; cancel it explicitly.
                getattr(ws, "_cancel_heartbeat", lambda: None)()
                self._protocol_mismatch()
                return
            hello = parse_hello(msg)
            ir.async_delete_issue(self.hass, DOMAIN, f"protocol_{self._subentry_id}")
            self.hello = hello
            self.state = dict(hello.state)
            self._set_connected(True)
            async for raw in ws:
                if raw.type == aiohttp.WSMsgType.TEXT:
                    try:
                        self._dispatch(parse_message(raw.data))
                    except ProtocolError as err:
                        _LOGGER.debug("Bad agent message: %s", err)
                elif raw.type in (aiohttp.WSMsgType.ERROR, aiohttp.WSMsgType.CLOSED):
                    break

    def _protocol_mismatch(self) -> None:
        _LOGGER.error(
            "Agent %s:%s speaks an unsupported protocol (need %s)",
            self._host,
            self._port,
            PROTOCOL_VERSION,
        )
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            f"protocol_{self._subentry_id}",
            is_fixable=False,
            severity=ir.IssueSeverity.ERROR,
            translation_key="protocol_mismatch",
        )

    def _dispatch(self, msg: dict[str, Any]) -> None:
        kind = msg["type"]
        if kind == "state":
            changes = msg.get("changes")
            if isinstance(changes, dict):
                self.state.update(changes)
                origin = str(msg.get("origin", "agent"))
                for cb in list(self._listeners):
                    cb(changes, origin)
        elif kind == "event":
            for cb in list(self._event_listeners):
                cb(str(msg.get("key")), str(msg.get("event_type")), msg.get("data") or {})
        elif kind == "log":
            for cb in list(self._log_listeners):
                cb(str(msg.get("message", "")), msg.get("data") or {})
        elif kind == "capabilities":
            capabilities, _ = parse_capabilities(msg.get("capabilities"))
            if self.hello is not None:
                self.hello.capabilities = capabilities
            for cb in list(self._capabilities_listeners):
                cb()
        elif kind == "result":
            fut = self._pending.get(msg.get("id"))
            if fut and not fut.done():
                if msg.get("ok"):
                    fut.set_result(msg.get("data") or {})
                else:
                    fut.set_exception(HomeAssistantError(str(msg.get("error", "error"))))
