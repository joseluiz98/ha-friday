"""The OTA poll loop: one poll per period, or sooner when kicked (button, WS, pairing).

A kick that arrives while a poll runs is kept: the event is cleared before the poll starts, so the kick re-arms it
and the loop polls again right after. A full kick wins over a light one until the next poll consumes it.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Callable

LOG = logging.getLogger("friday_server_agent.ota")


class Kick:
    def __init__(self) -> None:
        self._event = asyncio.Event()
        self._full = False

    def kick(self, full: bool = False) -> None:
        self._full = self._full or bool(full)
        self._event.set()

    def take(self) -> bool:
        """Consume the pending kick (called right before a poll); returns whether it asked for a full poll."""
        full, self._full = self._full, False
        self._event.clear()
        return full

    async def wait(self, timeout: float) -> None:
        try:
            await asyncio.wait_for(self._event.wait(), timeout)
        except asyncio.TimeoutError:
            pass


async def run(poll: Callable[[bool], object], kick: Kick, period: float, after=None) -> None:
    """`after` (async, optional) runs after every poll: the agent pushes the OTA state to HA there."""
    while True:
        full = kick.take()
        try:
            await asyncio.to_thread(poll, full)
        except asyncio.CancelledError:
            raise
        except Exception as err:  # noqa: BLE001 - the loop outlives any one bad poll
            LOG.warning("OTA cycle failed: %s", type(err).__name__)
        if after is not None:
            try:
                await after()
            except asyncio.CancelledError:
                raise
            except Exception as err:  # noqa: BLE001
                LOG.warning("OTA state push failed: %s", type(err).__name__)
        await kick.wait(period)
