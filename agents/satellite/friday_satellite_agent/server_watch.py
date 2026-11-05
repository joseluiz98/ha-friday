"""Tells the room (and the ring) when the F.R.I.D.A.Y. server cannot be reached (spec 2026-11-05, phrase `no_server`).

Every CHECK_S the agent opens a TCP connection to the host:port of `endereco_friday`. After FAILS_NEEDED failures in
a row it speaks once and turns the ring to the error state; a later success re-arms it.
"""

from __future__ import annotations

import asyncio
import json
import logging
from urllib.parse import urlparse

LOG = logging.getLogger("friday_satellite_agent")

CHECK_S = 60
FAILS_NEEDED = 3
CONNECT_TIMEOUT_S = 5


def server_target(config_path: str) -> tuple[str, int] | None:
    """(host, port) of `endereco_friday`, or None when the key is missing or unusable."""
    try:
        with open(config_path, encoding="utf-8") as f:
            url = json.load(f).get("endereco_friday")
        parsed = urlparse(url)
        host = parsed.hostname
        port = parsed.port or (443 if parsed.scheme in ("https", "wss") else 80)
    except (OSError, ValueError, TypeError, AttributeError):
        return None
    return (host, port) if host else None


async def tcp_reachable(host: str, port: int, timeout: float = CONNECT_TIMEOUT_S) -> bool:
    try:
        _r, w = await asyncio.wait_for(asyncio.open_connection(host, port), timeout)
    except (OSError, asyncio.TimeoutError):
        return False
    w.close()
    return True


class ServerWatch:
    def __init__(self, config_path: str, program, probe=tcp_reachable, sleep=asyncio.sleep) -> None:
        self._config_path, self._program, self._probe, self._sleep = config_path, program, probe, sleep
        self.fails = 0
        self.told = False

    async def step(self) -> None:
        target = server_target(self._config_path)
        if target is None:
            return
        if await self._probe(*target):
            self.fails, self.told = 0, False
            return
        self.fails += 1
        if self.fails >= FAILS_NEEDED and not self.told:
            self.told = True  # once per outage, even if the program refuses
            try:
                await self._program.command({"acao": "play_phrases", "keys": ["no_server"]})
                await self._program.command({"acao": "ring", "state": "error"})
            except Exception as err:  # noqa: BLE001
                LOG.warning("no_server notice failed: %s", type(err).__name__)

    async def run(self) -> None:
        while True:
            try:
                await self.step()
            except Exception as err:  # noqa: BLE001
                LOG.warning("server watch failed: %s", type(err).__name__)
            await self._sleep(CHECK_S)
