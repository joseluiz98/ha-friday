"""OTA HTTP API for satellites and WS commands for HA (spec 2026-11-10 §6, §8, §13).

Satellites authenticate with their own bearer token (issued at pairing, hashed on the VM). This is update protocol v1:
the contract that never breaks (`offer_payload`, `parse_report`; frozen examples in tests/contract/update_v1).
"""

from __future__ import annotations

import asyncio
import hmac
import logging
import re
from typing import Callable

from aiohttp import web

from .. import provisioning as pv
from .orchestrator import TAG, parse_report, offer_payload  # noqa: F401 - re-exported contract points

LOG = logging.getLogger("friday_server_agent.ota")
SAT_ID = re.compile(r"^sat-[0-9a-f]{6}$")


def _bearer(request: web.Request) -> str:
    h = request.headers.get("Authorization", "")
    return h[7:] if h.startswith("Bearer ") else ""


def add_routes(app: web.Application, orch, status_token: Callable[[], str]) -> None:
    def sat_ok(request: web.Request, sat_id) -> bool:
        return isinstance(sat_id, str) and bool(SAT_ID.match(sat_id)) and orch.tokens.check(sat_id, _bearer(request))

    async def offer(request: web.Request) -> web.StreamResponse:
        q = request.query
        if not sat_ok(request, q.get("sat_id")):
            return web.Response(status=401)
        try:
            proto = int(q.get("protocol", "1"))
        except ValueError:
            proto = 1
        installed = q.get("installed") or None
        if q.get("install") == "1":  # the satellite's own Install button: same as HA's (attempts zeroed, now)
            await asyncio.to_thread(orch.install, q["sat_id"])
        data = await asyncio.to_thread(orch.offer, q["sat_id"], q.get("channel", "stable"),
                                       installed if installed and TAG.match(installed) else None, proto,
                                       q.get("lock_sha256", ""))
        return web.json_response(data)

    async def package(request: web.Request) -> web.StreamResponse:
        if not sat_ok(request, request.query.get("sat_id")):
            return web.Response(status=401)
        path = await asyncio.to_thread(orch.package_file, request.match_info["tag"], request.match_info["name"])
        if not path:
            return web.Response(status=404)
        return web.FileResponse(path, headers={"Content-Type": "application/gzip"})

    async def report(request: web.Request) -> web.StreamResponse:
        try:
            body = await request.json()
        except ValueError:
            return web.json_response({"ok": False, "error": "json"}, status=400)
        if not sat_ok(request, body.get("sat_id") if isinstance(body, dict) else None):
            return web.Response(status=401)
        try:
            out = await asyncio.to_thread(orch.report, body)
        except ValueError as err:
            return web.json_response({"ok": False, "error": str(err)}, status=400)
        return web.json_response(out)

    async def request_install(request: web.Request) -> web.StreamResponse:
        want = status_token() or ""
        if not want or not hmac.compare_digest(_bearer(request).encode(), want.encode()):
            return web.Response(status=401)
        try:
            body = await request.json()
        except ValueError:
            body = None
        sat = body.get("sat_id") if isinstance(body, dict) else None
        if not isinstance(sat, str) or not SAT_ID.match(sat):
            return web.json_response({"ok": False, "error": "sat_id"}, status=400)
        target = await asyncio.to_thread(orch.install, sat)
        return web.json_response({"ok": True, "target": target})

    app.router.add_get("/v1/ota/offer", offer)
    app.router.add_get("/v1/ota/package/{tag}/{name}", package)
    app.router.add_post("/v1/ota/report", report)
    app.router.add_post("/v1/ota/request_install", request_install)


def make_handlers(orch, kick: Callable[[bool], None]):
    def args(cmd: dict) -> dict:
        return cmd["args"] if isinstance(cmd.get("args"), dict) else cmd

    async def status(cmd: dict) -> dict:
        return {"ok": True, "data": await asyncio.to_thread(orch.status)}

    async def check_now(cmd: dict) -> dict:
        kick(True)  # full re-check: moved tags, CI re-runs
        return {"ok": True}

    async def install(cmd: dict) -> dict:
        if args(cmd).get("component") == "server" or args(cmd).get("key") == "ota_server":  # key: the update entity
            await asyncio.to_thread(orch.install_server)
            kick(False)
            return {"ok": True}
        sat = args(cmd).get("sat_id")
        if not isinstance(sat, str) or not SAT_ID.match(sat):
            return {"ok": False, "error": "sat_id"}
        await asyncio.to_thread(orch.install, sat)
        return {"ok": True}

    async def set_auto(cmd: dict) -> dict:
        enabled = args(cmd).get("enabled")
        if not isinstance(enabled, bool):
            return {"ok": False, "error": "enabled"}
        await asyncio.to_thread(orch.set_auto, enabled)
        return {"ok": True}

    async def issue_token(cmd: dict) -> dict:
        sat = args(cmd).get("sat_id")
        if not isinstance(sat, str) or not SAT_ID.match(sat):
            return {"ok": False, "error": "sat_id"}
        try:
            token = await asyncio.to_thread(orch.tokens.issue, sat)
        except RuntimeError:  # TokenBookError: the file is unreadable and issuing would wipe the others
            return {"ok": False, "error": "token_book"}
        return {"ok": True, "data": {"token": token, "url": f"http://{pv.bundle_host()}"}}

    async def image_build(cmd: dict) -> dict:
        tag = args(cmd).get("tag")
        if tag is not None and (not isinstance(tag, str) or not TAG.match(tag)):
            return {"ok": False, "error": "tag"}
        out = await asyncio.to_thread(orch.image_build, tag)
        return {"ok": bool(out.get("ok")), **({"error": out["error"]} if out.get("error") else {})}

    async def image_status(cmd: dict) -> dict:
        return {"ok": True, "data": await asyncio.to_thread(orch.image_status)}

    return {"ota_status": status, "ota_check_now": check_now, "ota_install": install, "ota_set_auto": set_auto,
            "ota_issue_token": issue_token, "image_build": image_build, "image_status": image_status}
