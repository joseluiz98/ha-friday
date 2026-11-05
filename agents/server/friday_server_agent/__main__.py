"""F.R.I.D.A.Y. Server agent: samples the VM, the status service and Proxmox; pushes changes."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import ssl
import subprocess
import urllib.request

from aiohttp import web

from friday_agent_core.server import AgentCore

from . import collectors as c
from . import commands as cmds
from . import cost as costmod
from . import diagnostics as diagmod
from . import embeddings as embmod
from . import models as modelsmod
from . import provisioning as provmod
from . import reminders as remmod
from . import voice_switch as vsw

LOG = logging.getLogger("friday_server_agent")
SECRETS = os.environ.get("FRIDAY_SECRETS_DIR", "/srv/friday/secrets")
PORT = int(os.environ.get("FRIDAY_AGENT_PORT", "8801"))
# Proxmox read-only token file /srv/friday/secrets/proxmox_ro: ONE line, the whole Proxmox API token header value (PVEAuditor role),
# sent verbatim as Authorization. Format: see the Proxmox API token docs (user@realm!tokenid, then the token value).
OPENCLAW_JSON = os.environ.get("FRIDAY_OPENCLAW_JSON", "/srv/friday/home/.openclaw/openclaw.json")
ENV_FILE = "/srv/friday/friday.env"
PVE = "https://192.0.2.252:8006/api2/json/nodes/hypervisor"
# Touched by friday-memory-index.service (ExecStartPost) after each successful reindex.
# Written by the bot (activity.py): open sessions, brain turns in flight, heartbeat (minor 13 of 19/10).
ACTIVITY_FILE = os.environ.get("FRIDAY_ACTIVITY_FILE", "/srv/friday/state/activity.json")
MEMORY_INDEX_STAMP = os.environ.get("FRIDAY_MEMORY_INDEX_STAMP", "/srv/friday/state/memory-index.ok")


def _read(path: str) -> str:
    try:
        with open(path, encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


def _get(url: str, header: str, insecure: bool = False) -> dict:
    ctx = ssl._create_unverified_context() if insecure else None
    req = urllib.request.Request(url, headers={"Authorization": header})
    with urllib.request.urlopen(req, timeout=10, context=ctx) as r:
        return json.load(r)


def _proc(name: str) -> str:
    with open(f"/proc/{name}", encoding="utf-8") as f:
        return f.read()


def sample_vm() -> dict:
    v = os.statvfs("/")
    try:
        stamp = os.stat(MEMORY_INDEX_STAMP).st_mtime
    except OSError:
        stamp = None
    return {**c.vm_metrics(_proc("meminfo"), _proc("loadavg"),
                           (v.f_frsize, v.f_blocks, v.f_bfree, v.f_bavail),
                           _proc("uptime"), os.cpu_count() or 1),
            **c.memory_index_metrics(stamp)}


def _log_tail() -> str:
    r = subprocess.run(["docker", "logs", "--since", "6h", "friday"], stdout=subprocess.PIPE,
                       stderr=subprocess.STDOUT, timeout=15, text=True, errors="replace",
                       check=True)  # raises on docker failure: callers fail closed
    return r.stdout


def read_activity() -> dict | None:
    try:
        with open(ACTIVITY_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def sample_status(token: str) -> dict:
    import time
    status = _get("http://127.0.0.1:8800/v1/status", f"Bearer {token}")
    view = c.activity_view(read_activity(), time.time())
    if view is not None:
        status["conversation_open"] = view["conversation_open"]
        status["conversation_open_attributes"] = {"sessions": view["sessions"], "brain_turns": view["brain_turns"]}
    else:
        try:
            status["conversation_open"] = c.conversation_open(_log_tail())
            status["conversation_open_attributes"] = None
        except (OSError, subprocess.SubprocessError):
            pass
    return c.status_changes(status)


def sample_proxmox(header: str) -> dict:
    return c.proxmox_metrics(
        _get(f"{PVE}/status", header, True),
        _get(f"{PVE}/disks/smart?disk=/dev/nvme0n1", header, True),
        _get(f"{PVE}/tasks?typefilter=vzdump&limit=1&source=all", header, True))


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    token = _read(os.environ.get("FRIDAY_AGENT_TOKEN_FILE", f"{SECRETS}/agent_token"))
    if not token:
        raise SystemExit("missing agent token")
    status_token = _read(f"{SECRETS}/status_token")
    if not status_token:
        LOG.error("status token file %s/status_token is missing or empty", SECRETS)
        raise SystemExit("missing status token")
    pve = _read(f"{SECRETS}/proxmox_ro")
    switch = vsw.VoiceSwitch(os.environ.get("FRIDAY_VOICE_ENABLED_FILE", vsw.DEFAULT_PATH))
    state: dict = {"voice": switch.enabled}
    device = {"id": "server", "kind": "server", "name": "Server",
              "model": "Proxmox VM", "sw_version": c.VERSION}
    try:
        tracker = costmod.CostTracker()
    except ImportError:  # /opt/friday/cost missing: the other sensors keep working
        LOG.warning("cost modules not found; Gemini cost sensors disabled")
        tracker = None
    else:
        state["budget_brl"] = tracker.budget
    voice_set = vsw.make_set_handler(switch, state, lambda ch: core.push_state(ch, "ha"))
    budget_set = costmod.make_set_handler(tracker, state, lambda ch: core.push_state(ch, "ha")) if tracker else None

    async def set_dispatch(cmd: dict) -> dict:
        if cmd.get("key") == "budget_brl" and budget_set:
            return await budget_set(cmd)
        return await voice_set(cmd)

    store = modelsmod.ModelStore(
        os.environ.get("FRIDAY_STATE_DIR", "/srv/friday/state"),
        os.environ.get("FRIDAY_OPENCLAW_JSON", "/srv/friday/home/.openclaw/openclaw.json"),
        modelsmod.google_fetch(f"{SECRETS}/gemini_api_key"), _log_tail, cmds.compose_restart,
        env_file="/srv/friday/friday.env")

    store.reindexer = embmod.Reindexer(store)
    store.read_activity = read_activity

    bundles = provmod.BundleStore()

    core = AgentCore(token, device, lambda: c.capabilities(bool(pve)), lambda: dict(state),
                     {"set": set_dispatch,
                      "diagnostics": diagmod.make_handler(lambda: dict(state), lambda: diagmod.load_literals(SECRETS, OPENCLAW_JSON, ENV_FILE) + tuple(x for x in (token, status_token, pve) if x)),
                      **modelsmod.make_handlers(store),
                      **provmod.make_handlers(bundles),
                      **cmds.make_handlers(_log_tail, cmds.compose_restart,
                                           lambda m, d: core.push_log(m, d), read_activity)})

    async def loop(fn, arg, period, label, src):
        while True:
            try:
                changes = await asyncio.to_thread(fn, arg) if arg is not None else await asyncio.to_thread(fn)
                changes = src.ok(changes)
            except Exception as err:  # noqa: BLE001 - never log URLs/headers
                LOG.warning("%s sample failed: %s", label, type(err).__name__)
                changes = src.failed()
            if changes:
                state.update(changes)
                await core.push_state(changes, "agent")
            await asyncio.sleep(period)

    PVE_KEYS = list(c.proxmox_metrics({}, {}, {}))
    tasks = [loop(sample_vm, None, 30, "vm", c.Source(c.vm_metrics(
                 "MemTotal: 1 kB\n", "0", (1, 1, 1, 1), "1", 1))),
             loop(sample_status, status_token, 30, "status", c.Source(c.status_metrics({})))]
    if tracker:
        tasks.append(loop(lambda: tracker.refresh(), None, 60, "cost", c.Source([])))
    tasks.append(loop(remmod.sample, None, 60, "reminders", c.Source(["reminders"])))
    if pve:
        tasks.append(loop(sample_proxmox, pve, 300, "proxmox", c.Source(PVE_KEYS)))

    async def pending_loop():
        while True:
            try:
                if await asyncio.to_thread(store.apply_pending):
                    await core.push_log("Brain model applied; container restarted", {})
            except Exception as err:  # noqa: BLE001
                LOG.warning("pending brain change failed: %s", type(err).__name__)
            await asyncio.sleep(30)

    tasks.append(pending_loop())
    app = core.app()
    provmod.add_routes(app, bundles)  # GET /v1/bundles/{one_time_token}: its own single-use token, no other auth
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", PORT).start()
    await asyncio.gather(*tasks)


if __name__ == "__main__":
    asyncio.run(main())
