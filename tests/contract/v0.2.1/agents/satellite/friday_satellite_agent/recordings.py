"""Read-only routes serving activation recordings (bearer token, not loopback-restricted)."""

from __future__ import annotations

import asyncio
import hmac
import os
import re

from aiohttp import web

NAME = re.compile(r"^[A-Za-z0-9_-]+\.wav$")
STAMP = re.compile(r"(\d{8})_(\d{6})\.wav$")


def _authorized(request: web.Request, token: str) -> bool:
    header = request.headers.get("Authorization", "")
    return header.startswith("Bearer ") and hmac.compare_digest(header[7:].encode(), token.encode())


def _scan(folder: str) -> list[tuple[str, str, int]]:
    """(name, date, size) of every well-named wav with a trailing timestamp."""
    out = []
    try:
        with os.scandir(folder) as it:
            for e in it:
                m = STAMP.search(e.name)
                if not m or not NAME.match(e.name):
                    continue
                try:
                    out.append((e.name, m.group(1) + m.group(2), e.stat().st_size))
                except OSError:
                    continue
    except OSError:
        return []
    return out


def add_routes(app: web.Application, token: str, folder: str) -> None:
    def guard(handler):
        async def wrapped(request: web.Request) -> web.StreamResponse:
            if not _authorized(request, token):
                return web.Response(status=401)
            return await handler(request)
        return wrapped

    async def days(request):
        files = await asyncio.to_thread(_scan, folder)
        return web.json_response({"days": sorted({s[:8] for _, s, _ in files}, reverse=True)})

    async def listing(request):
        date = request.query.get("date", "")
        if not re.fullmatch(r"\d{8}", date):
            return web.Response(status=400)
        files = await asyncio.to_thread(_scan, folder)
        mine = sorted((f for f in files if f[1].startswith(date)), key=lambda f: (f[1], f[0]), reverse=True)
        return web.json_response({"recordings": [{"name": n, "size": z} for n, _, z in mine]})

    async def serve(request):
        name = request.match_info["name"]
        if not NAME.match(name):
            return web.Response(status=404)
        root = os.path.realpath(folder)
        path = os.path.realpath(os.path.join(root, name))
        if os.path.dirname(path) != root or not os.path.isfile(path):
            return web.Response(status=404)
        return web.FileResponse(path, headers={"Content-Type": "audio/wav"})

    app.router.add_get("/v1/recordings/days", guard(days))
    app.router.add_get("/v1/recordings", guard(listing))
    app.router.add_get("/v1/recordings/{name}", guard(serve))
