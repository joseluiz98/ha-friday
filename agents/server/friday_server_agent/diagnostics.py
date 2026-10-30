"""`diagnostics` command of the server agent: state + container log + agent journal."""

from __future__ import annotations

from typing import Awaitable, Callable

from friday_agent_core import diagnostics as d


async def collect(state: dict, literals: tuple[str, ...] = (), run=d.run_text) -> dict:
    return {
        "state": d.redact_data(state, literals),
        "logs": {
            "friday": await run(["docker", "logs", "--tail", "300", "friday"], 20, literals),
            "friday-server-agent": await run(
                ["journalctl", "-u", "friday-server-agent", "-n", "100", "--no-pager"], 15, literals),
        },
    }


def make_handler(state_fn: Callable[[], dict], literals: tuple[str, ...] = ()) -> Callable[[dict], Awaitable[dict]]:
    async def handler(cmd: dict) -> dict:
        return {"ok": True, "data": await collect(state_fn(), literals)}

    return handler
