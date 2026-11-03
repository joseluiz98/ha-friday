"""`diagnostics` command of the server agent: state + container log + agent journal."""

from __future__ import annotations

import json
import os
from typing import Awaitable, Callable

from friday_agent_core import diagnostics as d


def load_literals(secrets_dir: str, openclaw_json: str, env_file: str) -> tuple[str, ...]:
    """Secret values read from disk, never printed: every file in the secrets dir (Gemini key,
    Telegram token, agent/status/proxmox tokens), sensitive values of openclaw.json and the env file."""
    found: set[str] = set()
    try:
        for name in os.listdir(secrets_dir):
            path = os.path.join(secrets_dir, name)
            try:
                if os.path.isfile(path) and os.path.getsize(path) <= 8192:
                    with open(path, encoding="utf-8") as f:
                        for line in f.read().splitlines():
                            if len(line.strip()) >= 4:
                                found.add(line.strip())
            except (OSError, UnicodeDecodeError):
                continue
    except OSError:
        pass
    try:
        with open(openclaw_json, encoding="utf-8") as f:
            found.update(d.collect_literals(json.load(f)))
    except (OSError, ValueError):
        pass
    try:
        with open(env_file, encoding="utf-8") as f:
            for line in f:
                k, sep, v = line.strip().partition("=")
                v = v.strip().strip("\"'")
                if sep and len(v) >= 4 and d.is_sensitive_key(k):
                    found.add(v)
    except OSError:
        pass
    return tuple(sorted(found, key=len, reverse=True))


async def collect(state: dict, literals: tuple[str, ...] = (), run=d.run_text) -> dict:
    return {
        "state": d.redact_data(state, literals),
        "logs": {
            "friday": await run(["docker", "logs", "--tail", "300", "friday"], 20, literals),
            "friday-server-agent": await run(
                ["journalctl", "-u", "friday-server-agent", "-n", "100", "--no-pager"], 15, literals),
        },
    }


def make_handler(state_fn: Callable[[], dict], literals: Callable[[], tuple[str, ...]] = lambda: ()) -> Callable[[dict], Awaitable[dict]]:
    async def handler(cmd: dict) -> dict:
        return {"ok": True, "data": await collect(state_fn(), literals())}

    return handler
