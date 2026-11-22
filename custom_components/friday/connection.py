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
CONNECT_TIMEOUT = 5.0  # an unreachable address must not hold the fallback to the next one for minutes
CABLE_PROBE_S = 10.0  # while on the Wi-Fi, how often the cable address is probed
CABLE_STABLE_S = 60.0  # the cable must answer this long, without a miss, before the connection moves back
PROBE_TIMEOUT = 3.0


async def _close_ws(ws: aiohttp.ClientWebSocketResponse) -> None:
    await ws.close()
    # aiohttp can re-arm its heartbeat timer after close() when the last frame was just received; cancel it explicitly.
    getattr(ws, "_cancel_heartbeat", lambda: None)()


def _remover(listeners: list, cb: Callable) -> Callable[[], None]:
    listeners.append(cb)

    def remove() -> None:
        if cb in listeners:
            listeners.remove(cb)

    return remove


class FridayConnection:
    """Keeps a websocket to an agent alive and dispatches its messages."""

    def __init__(
        self,
        hass: HomeAssistant,
        hosts: list[str] | str,
        port: int,
        token: str,
        subentry_id: str,
    ) -> None:
        self.hass = hass
        # preferred first (the cable), then the fallback (the Wi-Fi); a bare str is one host
        self._hosts: list[str] = [hosts] if isinstance(hosts, str) else list(hosts)
        self._host = self._hosts[0]  # the address in use (or last tried)
        self._port = port
        self._watch_task: asyncio.Task | None = None
        self._reader: asyncio.Task | None = None
        self._hello_seen = False  # a host answered with a hello during the current connect round
        self._wake = asyncio.Event()
        self._announced_host: str | None = None
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
        for extra in (self._watch_task, self._reader):
            if extra is not None:
                extra.cancel()
        self._watch_task = self._reader = None
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
            try:
                cb(value)
            except Exception:  # noqa: BLE001 - one bad listener must not fail stop() and with it the entry's unload
                _LOGGER.exception("Availability listener failed for %s", self._subentry_id)

    @property
    def subentry_id(self) -> str:
        return self._subentry_id

    @property
    def host(self) -> str:
        """The address the connection uses (the one last tried while it is down)."""
        return self._host

    def _ensure_cable_watch(self) -> None:
        """Start the cable watch if the session is on a fallback address and none is running."""
        ws = self._ws
        if (
            not self.connected or ws is None or ws.closed
            or len(self._hosts) < 2 or self._host == self._hosts[0]
            or (self._watch_task is not None and not self._watch_task.done())
        ):
            return
        self._watch_task = self.hass.async_create_background_task(
            self._cable_watch(ws), f"friday cable watch {self._subentry_id}"
        )

    def _end_session(self) -> None:
        """Make the running session finish; it closes its own websocket (one closer, never two at once) and the loop
        reconnects on the current address list."""
        if self._reader is not None:
            self._reader.cancel()

    @property
    def hosts(self) -> list[str]:
        """The addresses in order of preference."""
        return list(self._hosts)

    def set_hosts(self, hosts: list[str], port: int) -> None:
        """New addresses from discovery: never reloads the entry. A session on a still-listed address is kept; one on
        a removed address (or port) is closed and the loop reconnects on the new list."""
        hosts = list(hosts)
        if hosts == self._hosts and port == self._port:
            return
        self._hosts, in_use_port, self._port = hosts, self._port, port
        if self.connected and self._ws is not None and not self._ws.closed:
            if self._host not in hosts or in_use_port != port:
                self._end_session()
            else:
                self._ensure_cable_watch()  # the cable may just have appeared (or become preferred again)
        else:
            self._wake.set()  # down and waiting out a backoff: try the new list now

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

    async def agent_get(
        self, path: str, params: dict | None = None, timeout: float = 15
    ) -> tuple[int, bytes, str]:
        """GET an HTTP route of the agent; returns (status, body, content type)."""
        session = async_get_clientsession(self.hass)
        async with session.get(
            f"http://{self._host}:{self._port}{path}", params=params,
            headers={"Authorization": f"Bearer {self._token}"},
            timeout=aiohttp.ClientTimeout(total=timeout),
        ) as resp:
            body = await resp.read()
            return resp.status, body, resp.content_type

    async def _run(self) -> None:
        backoff = BACKOFF_START
        while True:
            self._hello_seen = False
            for host in list(self._hosts):
                self._host = host
                try:
                    await self._session(host)
                    break  # the session ended: start over from the preferred address
                except asyncio.CancelledError:
                    raise
                except (aiohttp.ClientError, OSError, TimeoutError, ProtocolError) as err:
                    _LOGGER.debug("Agent %s:%s connection failed: %s", host, self._port, err)
                except Exception:  # noqa: BLE001
                    _LOGGER.exception("Unexpected error in agent connection")
                self._set_connected(False)
                self._ws = None
            self._set_connected(False)
            self._ws = None
            if self._hello_seen:
                backoff = BACKOFF_START  # an agent answered this round, whatever happened to it afterwards
            if self._mismatch:
                self._mismatch = False
                backoff = BACKOFF_MAX * 5  # incompatible agent: retry rarely
            self._wake.clear()
            try:
                await asyncio.wait_for(self._wake.wait(), backoff)
            except TimeoutError:
                pass
            if not self._hello_seen:
                backoff = min(backoff * 2, BACKOFF_MAX)

    async def _session(self, host: str) -> bool:
        """One websocket session on `host`; True when it got as far as a hello (False: protocol mismatch)."""
        session = async_get_clientsession(self.hass)
        url = f"ws://{host}:{self._port}{WS_PATH}"
        ws = await asyncio.wait_for(
            session.ws_connect(
                url, headers={"Authorization": f"Bearer {self._token}"}, heartbeat=HEARTBEAT
            ),
            CONNECT_TIMEOUT,
        )
        try:
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
                await _close_ws(ws)
                self._protocol_mismatch()
                return False
            hello = parse_hello(msg)
            self._hello_seen = True
            ir.async_delete_issue(self.hass, DOMAIN, f"protocol_{self._subentry_id}")
            previous, self.hello = self.hello, hello
            self.state = dict(hello.state)
            if self._announced_host != host:
                _LOGGER.info("Agent %s now on %s", self._subentry_id, host)
                self._announced_host = host
            self._set_connected(True)
            if previous is not None and previous.capabilities != hello.capabilities:
                # the agent came back with a different capability list (no "capabilities" message): rebuild entities
                for cb in list(self._capabilities_listeners):
                    cb()
            self._ensure_cable_watch()
            reader = self.hass.async_create_background_task(
                self._read(ws), f"friday reader {self._subentry_id}"
            )
            self._reader = reader
            try:
                await asyncio.wait({reader})
                if reader.done() and not reader.cancelled():
                    reader.result()  # an unexpected error in the loop surfaces here
            finally:
                self._reader = None
                reader.cancel()
                task, self._watch_task = self._watch_task, None
                if task is not None and task is not asyncio.current_task():
                    task.cancel()
        finally:
            await _close_ws(ws)  # also cancels the heartbeat timer the peer's close frame can re-arm
        return True

    async def _read(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        async for raw in ws:
            if raw.type == aiohttp.WSMsgType.TEXT:
                try:
                    self._dispatch(parse_message(raw.data))
                except ProtocolError as err:
                    _LOGGER.debug("Bad agent message: %s", err)
            elif raw.type in (aiohttp.WSMsgType.ERROR, aiohttp.WSMsgType.CLOSED):
                break

    async def _probe(self, host: str, port: int) -> bool:
        """Does `host` accept a TCP connection on the agent's port?"""
        try:
            _, writer = await asyncio.wait_for(asyncio.open_connection(host, port), PROBE_TIMEOUT)
        except (OSError, TimeoutError):
            return False
        writer.close()
        try:
            await asyncio.wait_for(writer.wait_closed(), 1.0)
        except (OSError, TimeoutError):
            pass
        return True

    async def _cable_watch(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        """On the fallback address: probe the preferred one and move back once it has answered CABLE_STABLE_S."""
        loop = asyncio.get_running_loop()
        ok_since: float | None = None
        watched: str | None = None
        while not ws.closed:
            await asyncio.sleep(CABLE_PROBE_S)
            preferred = self._hosts[0]
            if preferred == self._host:
                return  # the list changed under us: the preferred address is the one in use
            if preferred != watched:
                watched, ok_since = preferred, None
            if await self._probe(preferred, self._port):
                now = loop.time()
                if ok_since is None:
                    ok_since = now
                if now - ok_since >= CABLE_STABLE_S:
                    _LOGGER.info("Agent %s: %s answers again, moving back", self._subentry_id, preferred)
                    self._end_session()
                    return
            else:
                ok_since = None

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
