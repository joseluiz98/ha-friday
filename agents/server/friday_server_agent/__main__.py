"""F.R.I.D.A.Y. Server agent: samples the VM, the status service and Proxmox; pushes changes."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import ssl
import subprocess
import urllib.parse
import urllib.request

from aiohttp import web

from friday_agent_core.cpu import CpuUsage
from friday_agent_core.server import AgentCore

from . import collectors as c
from . import announce as announcemod
from . import commands as cmds
from . import cost as costmod
from . import diagnostics as diagmod
from . import embeddings as embmod
from . import models as modelsmod
from . import provisioning as provmod
from . import voices as voicesmod
from . import reminders as remmod
from . import voice_switch as vsw
from .ota import github as otagh
from .ota import http as otahttp
from .ota import tls as otatls
from .ota import image as otaimg
from .ota import loop as otaloop
from .ota import notify as otanotify
from .ota import orchestrator as otaorc
from .ota import proxmox as otapve
from .ota import server_install as otasrv
from .ota import repo as otarepo
from .ota import store as otastore
from friday_agent_core.ota.constants import POLL_GITHUB_S

LOG = logging.getLogger("friday_server_agent")
SECRETS = os.environ.get("FRIDAY_SECRETS_DIR", "/srv/friday/secrets")
PORT = int(os.environ.get("FRIDAY_AGENT_PORT", "8801"))
# Proxmox read-only token file /srv/friday/secrets/proxmox_ro: ONE line, the whole Proxmox API token header value (PVEAuditor role),
# sent verbatim as Authorization. Format: see the Proxmox API token docs (user@realm!tokenid, then the token value).
OPENCLAW_JSON = os.environ.get("FRIDAY_OPENCLAW_JSON", "/srv/friday/home/.openclaw/openclaw.json")
ENV_FILE = "/srv/friday/friday.env"
PVE = os.environ.get("FRIDAY_PVE_API", "")  # e.g. https://<proxmox>:8006/api2/json/nodes/<node>; from /srv/friday/state/local.env
PVE_BACKUP_STORAGE = os.environ.get("FRIDAY_PVE_BACKUP_STORAGE", "backup")  # the vzdump job's target storage
# Written by the bot (activity.py): open sessions, brain turns in flight, heartbeat (minor 13 of 19/10).
ACTIVITY_FILE = os.environ.get("FRIDAY_ACTIVITY_FILE", "/srv/friday/state/activity.json")
# 09/11/2026: nightly wake-word cache. featcache_nightly.sh touches the stamp only after caching every pending day of
# every satellite, and keeps {"<sat_id>": "<YYYY-MM-DD>"} of each satellite's newest cached day in the registry (18-19/11).
WAKEWORD_CACHE_STAMP = os.environ.get("FRIDAY_WAKEWORD_CACHE_STAMP", "/srv/friday/state/wwt-featcache.ok")
WAKEWORD_CACHE_REGISTRY = os.environ.get("FRIDAY_WAKEWORD_CACHE_REGISTRY", "/srv/friday/state/wwt-featcache.json")


def _mtime(path: str):
    try:
        return os.stat(path).st_mtime
    except OSError:
        return None


def _read(path: str) -> str:
    try:
        with open(path, encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


def _read_json(path: str):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _get(url: str, header: str, insecure: bool = False) -> dict:
    ctx = ssl._create_unverified_context() if insecure else None
    req = urllib.request.Request(url, headers={"Authorization": header})
    with urllib.request.urlopen(req, timeout=10, context=ctx) as r:
        return json.load(r)


def _proc(name: str) -> str:
    with open(f"/proc/{name}", encoding="utf-8") as f:
        return f.read()


CPU = CpuUsage()  # one per process: the first sample after a start has no delta (CPU Usage unknown)


def sample_vm() -> dict:
    v = os.statvfs("/")
    return {**c.vm_metrics(_proc("meminfo"), _proc("loadavg"),
                           (v.f_frsize, v.f_blocks, v.f_bfree, v.f_bavail),
                           _proc("uptime"), os.cpu_count() or 1, cpu_pct=CPU.update(_proc("stat"))),
            **c.wakeword_pipeline_metrics(_mtime(WAKEWORD_CACHE_STAMP), _read_json(WAKEWORD_CACHE_REGISTRY))}


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


def _backup_runs(header: str, limit: int = 8) -> list:
    """Latest vzdump tasks with their log text (per-guest outcome lives only in the log)."""
    runs = []
    for t in _get(f"{PVE}/tasks?typefilter=vzdump&limit={limit}&source=all", header, True).get("data") or []:
        upid = urllib.parse.quote(t.get("upid", ""), safe="")
        log = _get(f"{PVE}/tasks/{upid}/log?limit=5000", header, True).get("data") or []
        runs.append((t, [ln.get("t", "") for ln in log]))
    return runs


def sample_proxmox(header: str) -> dict:
    return c.proxmox_metrics(
        _get(f"{PVE}/status", header, True),
        _get(f"{PVE}/disks/smart?disk=/dev/nvme0n1", header, True),
        _get(f"{PVE}/tasks?typefilter=vzdump&limit=1&source=all", header, True),
        _backup_runs(header),
        _get(f"{PVE}/storage/{PVE_BACKUP_STORAGE}/status", header, True),
        {"data": _get(f"{PVE}/qemu", header, True).get("data", []) + _get(f"{PVE}/lxc", header, True).get("data", [])})


def ota_entities(orch) -> dict:
    """The server's OTA entities: its update, the integration's target (Task 18 installs it through HACS), the newest
    provisioning image (state = tag; attributes built_at/status/error/files), the satellites held by the server and
    the alpha promotion clock (state = promotion time; attributes tag/status/started_at/promotes_at/soaked_hours/
    newer_stable/alphas) and its switch (`ota_alpha_promotion_allowed`: True allowed, False blocked by the owner,
    None when there is nothing to allow)."""
    img = orch.image_status() or {}
    promotion, promotion_attrs = orch.alpha_promotion()
    return {"ota_server": orch.server_entity(),
            "ota_integration_target": orch.status()["server"].get("integration_target"),
            "image_latest": img.get("tag"),
            "ota_waiting_server": len(waiting := orch.waiting_server()),
            "ota_waiting_server_attributes": {"satellites": waiting},
            "ota_alpha_promotion": promotion,
            "ota_alpha_promotion_attributes": promotion_attrs,
            "ota_alpha_promotion_allowed": orch.promotion_allowed(),
            "image_latest_attributes": {k: img.get(k) for k in ("built_at", "status", "error", "files")}}


def make_alpha_promotion_handler(orch, ota_state_push, push_log):
    """`alpha_promotion` command and the Alpha promotion switch: the owner blocks or allows an alpha's promotion.
    Returns async (cmd) -> {"ok": True, "tag"} | {"ok": False, "error"}; `tag` absent = the tracked alpha."""
    async def handler(cmd: dict) -> dict:
        tag, allowed = cmd.get("tag"), cmd.get("allowed")
        if not isinstance(allowed, bool):
            return {"ok": False, "error": "value"}
        if tag is not None and not isinstance(tag, str):
            return {"ok": False, "error": "unknown_tag"}
        out = await asyncio.to_thread(orch.set_promotion, tag, allowed)
        if out.get("ok"):
            await ota_state_push()
            await push_log(f"Alpha promotion of {out['tag']} {'allowed' if allowed else 'blocked'} by the owner",
                           {"tag": out["tag"]})
        return out
    return handler


def make_set_dispatch(orch, state: dict, push_state, ota_state_push, push_log, fallback):
    """The `set` command: the OTA switches, then `fallback` (budget / voice)."""
    promotion = make_alpha_promotion_handler(orch, ota_state_push, push_log)

    async def set_dispatch(cmd: dict) -> dict:
        if cmd.get("key") == "ota_auto_install":
            value = cmd.get("value")
            if not isinstance(value, bool):
                return {"ok": False, "error": "value"}
            await asyncio.to_thread(orch.set_auto, value)
            state["ota_auto_install"] = value
            await push_state({"ota_auto_install": value}, "ha")
            return {"ok": True}
        if cmd.get("key") == "ota_alpha_promotion_allowed":
            out = await promotion({"tag": None, "allowed": cmd.get("value")})
            return {"ok": True} if out.get("ok") else out
        return await fallback(cmd)
    return set_dispatch


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

    ota_store = otastore.OtaStore()
    try:
        vault = provmod._vault()
    except (OSError, ImportError) as err:  # without the vault no package builds; the rest of the agent still runs
        LOG.error("OTA: vault module unavailable: %s", type(err).__name__)
        vault = None
    chat = os.environ.get("FRIDAY_TELEGRAM_CHAT", "")
    notifier = otanotify.notifier_for(chat)
    ota_repo = otarepo.VmRepo()
    try:  # snapshot-only token (spec §16); without it a heavy server update fails before changing anything
        snapshots = otapve.ProxmoxSnapshots(PVE, os.environ["FRIDAY_VMID"], otapve.load_token()) if PVE else None
    except (OSError, KeyError) as err:
        LOG.warning("OTA: no Proxmox snapshot access (%s)", type(err).__name__)
        snapshots = None
    gh_token_file = os.environ.get("FRIDAY_GITHUB_TOKEN_FILE", "/srv/friday/state/github_token")
    publisher = otagh.GitHubPublisher(otagh.repo_from_env(), gh_token_file)  # token read again on every promotion
    LOG.info("OTA: automatic promotion: %s", {
        "ok": "on", "missing": "waiting for the GitHub token file (read again before each try)",
        "open_permissions": "on, but the GitHub token file is readable by others (chmod 600)"}[publisher.check()])
    installer = otasrv.ServerInstaller(ota_store, ota_repo, snapshots, lambda: status_token)
    orch = otaorc.Orchestrator(ota_store, otastore.TokenBook(),
                               otagh.GitHubWatcher(os.environ.get("FRIDAY_OTA_REPO", "joseluiz98/ha-friday")),
                               ota_repo, vault, notifier, installer=installer, images=otaimg.ImageBuilder(),
                               publisher=publisher)
    state["ota_auto_install"] = ota_store.auto_install
    def ota_state() -> dict:
        return ota_entities(orch)

    state.update(ota_state())

    async def ota_state_push() -> None:
        changes = {k: v for k, v in (await asyncio.to_thread(ota_state)).items() if state.get(k) != v}
        if changes:
            state.update(changes)
            await core.push_state(changes, "agent")
    try:
        otatls.ensure()
        ota_tls = (otatls.PORT, otatls.fingerprint())
    except (OSError, ValueError) as err:
        LOG.error("OTA: no TLS certificate (%s); satellites get no credentials until it exists", type(err).__name__)
        ota_tls = None
    ota_kick = otaloop.Kick()
    kick = ota_kick.kick

    async def other_set(cmd: dict) -> dict:
        if cmd.get("key") == "budget_brl" and budget_set:
            return await budget_set(cmd)
        return await voice_set(cmd)

    promotion_log = lambda msg, data: core.push_log(msg, data)  # noqa: E731 - core is built below
    set_dispatch = make_set_dispatch(orch, state, lambda ch, origin: core.push_state(ch, origin), ota_state_push,
                                     promotion_log, other_set)

    store = modelsmod.ModelStore(
        os.environ.get("FRIDAY_STATE_DIR", "/srv/friday/state"),
        os.environ.get("FRIDAY_OPENCLAW_JSON", "/srv/friday/home/.openclaw/openclaw.json"),
        modelsmod.google_fetch(f"{SECRETS}/gemini_api_key"), _log_tail, cmds.compose_restart,
        env_file="/srv/friday/friday.env")

    store.reindexer = embmod.Reindexer(store)
    store.read_activity = read_activity

    bundles = provmod.BundleStore(consolidate=voicesmod.consolidate, package_for_update=orch.package_for_update)
    cmd_handlers = cmds.make_handlers(_log_tail, cmds.compose_restart, lambda m, d: core.push_log(m, d), read_activity)

    async def press_dispatch(cmd: dict) -> dict:
        if cmd.get("key") == "ota_check_now":
            kick(True)
            return {"ok": True}
        if cmd.get("key") == "image_build":
            out = await asyncio.to_thread(orch.image_build, None)
            await ota_state_push()
            return {"ok": bool(out.get("ok")), **({"error": out["error"]} if out.get("error") else {})}
        return await cmd_handlers["press"](cmd)

    core = AgentCore(token, device, lambda: c.capabilities(bool(pve)), lambda: dict(state),
                     {"set": set_dispatch,
                      "alpha_promotion": make_alpha_promotion_handler(orch, ota_state_push, promotion_log),
                      "diagnostics": diagmod.make_handler(lambda: dict(state), lambda: diagmod.load_literals(SECRETS, OPENCLAW_JSON, ENV_FILE) + tuple(x for x in (token, status_token, pve) if x)),
                      **modelsmod.make_handlers(store),
                      **provmod.make_handlers(bundles),
                      **cmd_handlers, **announcemod.make_handlers(), "press": press_dispatch,
                      **otahttp.make_handlers(orch, kick, lambda: ota_tls)})

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

    tasks.append(otaloop.run(orch.poll, ota_kick, POLL_GITHUB_S, after=ota_state_push))

    async def idle_loop():  # continuous idle for heavy server updates (HEAVY_IDLE_S)
        while True:
            try:
                await asyncio.to_thread(installer.sample_idle)
            except Exception as err:  # noqa: BLE001
                LOG.warning("OTA idle sample failed: %s", type(err).__name__)
            await asyncio.sleep(30)

    tasks.append(idle_loop())
    app = core.app()
    otahttp.add_routes(app, orch, lambda: status_token)  # satellites: their own tokens; request_install: status token
    provmod.add_routes(app, bundles)  # GET /v1/bundles/{one_time_token}: its own single-use token, no other auth
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", PORT).start()
    if ota_tls is not None:  # the satellites' OTA channel (the same routes, over TLS with a pinned certificate)
        await web.TCPSite(runner, "0.0.0.0", ota_tls[0], ssl_context=otatls.context()).start()
    await asyncio.gather(*tasks)


if __name__ == "__main__":
    asyncio.run(main())
