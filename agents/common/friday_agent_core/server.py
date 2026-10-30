"""Shared websocket server core for F.R.I.D.A.Y. agents (server and satellite).

Depends only on aiohttp. Never logs tokens or command arguments.
"""

from __future__ import annotations

import hmac
import logging
from typing import Awaitable, Callable

from aiohttp import WSMsgType, web

PROTOCOL_VERSION = 1  # keep in sync with custom_components/friday/protocol.py
_LOG = logging.getLogger(__name__)

Handler = Callable[[dict], Awaitable[dict]]


class AgentCore:
    def __init__(
        self,
        token: str,
        device: dict,
        capabilities_fn: Callable[[], list[dict]],
        state_fn: Callable[[], dict],
        handlers: dict[str, Handler],
    ) -> None:
        self._token = token
        self._device = device
        self._capabilities_fn = capabilities_fn
        self._state_fn = state_fn
        self._handlers = handlers
        self._clients: set[web.WebSocketResponse] = set()
        self._last: dict = {}

    def app(self) -> web.Application:
        app = web.Application()
        app.router.add_get("/v1/ws", self._ws)
        return app

    last_peer: str | None = None

    def _authorized(self, request: web.Request) -> bool:
        header = request.headers.get("Authorization", "")
        if not header.startswith("Bearer "):
            return False
        return hmac.compare_digest(
            header[7:].encode(), self._token.encode()
        )

    async def _ws(self, request: web.Request) -> web.StreamResponse:
        if not self._authorized(request):
            return web.Response(status=401)
        self.last_peer = request.remote  # the HA host, used to verify reachability after a Wi-Fi change
        ws = web.WebSocketResponse(heartbeat=30)
        await ws.prepare(request)
        state = dict(self._state_fn())
        await ws.send_json(
            {
                "type": "hello",
                "protocol": PROTOCOL_VERSION,
                "device": self._device,
                "capabilities": self._capabilities_fn(),
                "state": state,
            }
        )
        self._clients.add(ws)
        try:
            async for msg in ws:
                if msg.type != WSMsgType.TEXT:
                    continue
                try:
                    data = msg.json()
                except ValueError:
                    continue
                if isinstance(data, dict) and data.get("type") == "command":
                    await ws.send_json(await self._run(data))
        finally:
            self._clients.discard(ws)
        return ws

    async def _run(self, cmd: dict) -> dict:
        cid = cmd.get("id")
        name = cmd.get("name")
        handler = self._handlers.get(name) if isinstance(name, str) else None
        if handler is None:
            return {"type": "result", "id": cid, "ok": False, "error": "unknown_command"}
        try:
            res = await handler(cmd) or {}
        except Exception as err:  # noqa: BLE001 - never log args (may hold secrets)
            _LOG.error("handler %s failed: %s", name, type(err).__name__)
            return {"type": "result", "id": cid, "ok": False, "error": "internal"}
        out = {"type": "result", "id": cid, "ok": bool(res.get("ok", True))}
        if "data" in res:
            out["data"] = res["data"]
        if "error" in res:
            out["error"] = res["error"]
        return out

    async def _broadcast(self, payload: dict) -> None:
        for ws in list(self._clients):
            try:
                await ws.send_json(payload)
            except (ConnectionError, RuntimeError):
                self._clients.discard(ws)

    async def push_state(self, changes: dict, origin: str) -> None:
        diff = {k: v for k, v in changes.items() if k not in self._last or self._last[k] != v}
        if not diff:
            return
        self._last.update(diff)
        await self._broadcast({"type": "state", "changes": diff, "origin": origin})

    async def push_event(self, key: str, event_type: str, data: dict) -> None:
        await self._broadcast({"type": "event", "key": key, "event_type": event_type, "data": data})

    async def push_log(self, message: str, data: dict) -> None:
        await self._broadcast({"type": "log", "message": message, "data": data})

    async def push_capabilities(self) -> None:
        await self._broadcast({"type": "capabilities", "capabilities": self._capabilities_fn()})
