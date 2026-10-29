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

LOG = logging.getLogger("friday_server_agent")
SECRETS = os.environ.get("FRIDAY_SECRETS_DIR", "/srv/friday/secrets")
PORT = int(os.environ.get("FRIDAY_AGENT_PORT", "8801"))
# Proxmox read-only token file /srv/friday/secrets/proxmox_ro: ONE line, the whole Proxmox API token header value (PVEAuditor role),
# sent verbatim as Authorization. Format: see the Proxmox API token docs (user@realm!tokenid, then the token value).
PVE = "https://192.0.2.252:8006/api2/json/nodes/hypervisor"


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
    return c.vm_metrics(_proc("meminfo"), _proc("loadavg"),
                        (v.f_frsize, v.f_blocks, v.f_bfree, v.f_bavail),
                        _proc("uptime"), os.cpu_count() or 1)


def _log_tail() -> str:
    r = subprocess.run(["docker", "logs", "--since", "6h", "friday"], stdout=subprocess.PIPE,
                       stderr=subprocess.STDOUT, timeout=15, text=True, errors="replace")
    return r.stdout


def sample_status(token: str) -> dict:
    status = _get("http://127.0.0.1:8800/v1/status", f"Bearer {token}")
    try:
        status["conversation_open"] = c.conversation_open(_log_tail())
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
    pve = _read(f"{SECRETS}/proxmox_ro")
    state: dict = {}
    device = {"id": "server", "kind": "server", "name": "F.R.I.D.A.Y. Server",
              "model": "Proxmox VM", "sw_version": c.VERSION}
    core = AgentCore(token, device, lambda: c.capabilities(bool(pve)), lambda: dict(state), {})

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

    PVE_KEYS = ["host_available_mb", "nvme_temperature", "last_backup"]
    tasks = [loop(sample_vm, None, 30, "vm", c.Source(c.vm_metrics(
                 "MemTotal: 1 kB\n", "0", (1, 1, 1, 1), "1", 1))),
             loop(sample_status, status_token, 30, "status", c.Source(c.status_metrics({})))]
    if pve:
        tasks.append(loop(sample_proxmox, pve, 300, "proxmox", c.Source(PVE_KEYS)))
    runner = web.AppRunner(core.app())
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", PORT).start()
    await asyncio.gather(*tasks)


if __name__ == "__main__":
    asyncio.run(main())
