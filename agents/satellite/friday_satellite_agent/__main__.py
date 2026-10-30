"""F.R.I.D.A.Y. satellite agent: mirrors the voice program's settings to Home Assistant."""

from __future__ import annotations

import asyncio
import datetime
import json
import logging
import os

from aiohttp import web

from friday_agent_core.server import AgentCore

from . import capabilities as cap
from . import local
from .announce import announce
from .program import ProgramClient, ProgramError

LOG = logging.getLogger("friday_satellite_agent")
CONFIG = os.environ.get("FRIDAY_SAT_CONFIG", "/etc/friday-satelite/config.json")
ID_FILE = os.environ.get("FRIDAY_SAT_ID_FILE", "/etc/friday-satelite/id")
PORT = int(os.environ.get("FRIDAY_AGENT_PORT", "8701"))
POLL_S = 20


def _read_settings() -> tuple[str, int]:
    with open(CONFIG, encoding="utf-8") as f:  # only the keys we need; never logged
        cfg = json.load(f)
    return cfg["senha_porta_local"], int(cfg.get("porta_local", 8700))


def _sat_id() -> str:
    with open(ID_FILE, encoding="utf-8") as f:
        return f.read().strip()


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    token, program_port = _read_settings()
    sat_id = _sat_id()
    program = ProgramClient(token, f"http://127.0.0.1:{program_port}")
    mapper, tracker = cap.Mapper(), cap.Tracker()
    memo: dict = {"cfg": None, "ww": [], "state": {"program_state": "off", **tracker.counters(),
                                                   "sw_version": cap.VERSION}}
    device = {"id": sat_id, "kind": "satellite", "name": f"F.R.I.D.A.Y. Satellite {sat_id}",
              "model": "Raspberry Pi 3 + Jabra", "sw_version": cap.VERSION}

    def capabilities() -> list[dict]:
        return cap.controls_from_config(memo["cfg"] or {}, memo["ww"])

    async def refresh(origin: str) -> None:
        status = await program.status()
        if status is None:
            memo["cfg"] = None
            changes = {"program_state": "off"}
        else:
            try:
                memo["cfg"] = await program.get_config()
                memo["ww"] = await program.wakewords()
            except (ProgramError, OSError):
                return
            changes = mapper.state(memo["cfg"], memo["ww"], status)
        changes.update(tracker.counters())
        memo["state"].update(changes)
        await core.push_state(changes, origin)

    async def do_set(cmd: dict) -> dict:
        cfg = memo["cfg"]
        if cfg is None:
            return {"ok": False, "error": "program_down"}
        plan = mapper.to_program(cfg, cmd.get("key"), cmd.get("value"))
        if plan is None:
            return {"ok": False, "error": "unknown_key"}
        try:
            if "config" in plan:
                tracker.mark_ha(plan["config"])
                await program.put_config(plan["config"])
            else:
                name, mode = plan["wakeword"]
                tracker.mark_ha(["modo_friday", "palavras_extras"])
                await program.set_wakeword(name, modo=mode)
        except ProgramError as err:
            return {"ok": False, "error": "rejected" if err.status in (400, 409) else "program_error"}
        except (OSError, TimeoutError):
            return {"ok": False, "error": "program_down"}
        await refresh("ha")
        return {"ok": True}

    async def on_event(tipo: str, dados: dict) -> None:
        out = tracker.on_event(tipo, dados)
        if not out:
            return
        if "config" in out:
            await refresh(out["origin"])
            return
        if out.get("state"):
            memo["state"].update(out["state"])
            await core.push_state(out["state"], "voice")
        if out.get("event"):
            await core.push_event(*out["event"])

    core = AgentCore(token, device, capabilities, lambda: dict(memo["state"]), {"set": do_set})

    async def poll() -> None:
        sent_caps = None
        while True:
            try:
                await refresh("agent")
                caps = capabilities()
                if sent_caps is not None and caps != sent_caps:
                    await core.push_capabilities()
                sent_caps = caps
            except Exception as err:  # noqa: BLE001
                LOG.warning("poll failed: %s", type(err).__name__)
            await asyncio.sleep(POLL_S)

    async def midnight() -> None:
        while True:
            now = datetime.datetime.now()
            nxt = (now + datetime.timedelta(days=1)).replace(hour=0, minute=0, second=1, microsecond=0)
            await asyncio.sleep((nxt - now).total_seconds())
            changes = tracker.reset_day()
            memo["state"].update(changes)
            await core.push_state(changes, "system")

    app = local.make_app(token, on_event, core.app())
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", PORT).start()
    zc = await asyncio.to_thread(announce, sat_id, PORT)
    try:
        await asyncio.gather(poll(), midnight())
    finally:
        if zc:
            zc.close()


if __name__ == "__main__":
    asyncio.run(main())
