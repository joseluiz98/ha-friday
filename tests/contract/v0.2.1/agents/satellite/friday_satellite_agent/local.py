"""Local event route: the satellite program POSTs here (loopback only, same password)."""

from __future__ import annotations

import hmac
from typing import Awaitable, Callable

from aiohttp import web

PATH = "/v1/local/event"


def is_loopback(remote: str | None) -> bool:
    return remote in ("127.0.0.1", "::1")


def make_app(token: str, on_event: Callable[[str, dict], Awaitable[None]], app: web.Application | None = None):
    async def handle(request: web.Request) -> web.Response:
        header = request.headers.get("Authorization", "")
        if not header.startswith("Bearer ") or not hmac.compare_digest(header[7:].encode(), token.encode()):
            return web.Response(status=401)
        if not is_loopback(request.remote):
            return web.Response(status=403)
        try:
            body = await request.json()
        except ValueError:
            return web.Response(status=400)
        tipo, dados = (body.get("tipo"), body.get("dados")) if isinstance(body, dict) else (None, None)
        if not isinstance(tipo, str) or not isinstance(dados, dict):
            return web.Response(status=400)
        await on_event(tipo, dados)
        return web.Response(status=204)

    app = app if app is not None else web.Application()
    app.router.add_post(PATH, handle)
    return app
