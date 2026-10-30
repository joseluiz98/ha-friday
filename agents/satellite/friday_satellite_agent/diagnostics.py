"""`diagnostics` command of the satellite: redacted config + journal tails."""

from __future__ import annotations

import json
from typing import Awaitable, Callable

from friday_agent_core import diagnostics as d

JOURNALS = (("friday-satelite", 300), ("friday-satellite-agent", 100))


async def collect(config_path: str, state: dict, run=d.run_text) -> dict:
    literals: tuple[str, ...] = ()
    try:
        with open(config_path, encoding="utf-8") as f:
            cfg = json.load(f)
        literals = d.collect_literals(cfg)
        config = d.redact_data(cfg, literals)
    except (OSError, ValueError):
        config = "unavailable"
    logs = {unit: await run(["journalctl", "-u", unit, "-n", str(n), "--no-pager"], 15, literals)
            for unit, n in JOURNALS}
    return {"config": config, "state": d.redact_data(state, literals), "logs": logs}


def make_handler(config_path: str, state_fn: Callable[[], dict]) -> Callable[[dict], Awaitable[dict]]:
    async def handler(cmd: dict) -> dict:
        return {"ok": True, "data": await collect(config_path, state_fn())}

    return handler
