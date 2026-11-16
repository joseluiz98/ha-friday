"""LED ring preview (15/11): every ring state, one after the other, so the owner can see them from HA with one click.

It only asks the program (`{"acao": "ring", ...}`), the ring's single owner, and only while the program is idle; a
conversation that starts mid-preview ends it (the program's own state then takes the ring back).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable

LOG = logging.getLogger("friday_satellite_agent.ring_preview")

SEQUENCE = ("off", "update_pending", "listen", "think", "speak", "dnd", "muted", "alarm", "setup", "updating",
            "error", "net_error")
STEP_S = 10
COUNTDOWN_S = 10


class RingPreview:
    def __init__(self, command: Callable[[dict], Awaitable], program_state: Callable[[], str | None],
                 sleep: Callable[[float], Awaitable] = asyncio.sleep) -> None:
        self._command, self._state, self._sleep = command, program_state, sleep
        self._task: asyncio.Task | None = None

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def start(self) -> dict:
        if self.running:
            return {"ok": False, "error": "busy"}
        if self._state() != "idle":
            return {"ok": False, "error": "not_idle"}
        self._task = asyncio.create_task(self.run())
        return {"ok": True}

    async def run(self) -> str:
        """'done', or 'interrupted' when the program left idle. The preview shows one state at a time (`preview`); it always
        ends with `state: None`, which hands the ring back to the composition (mute, DND, update pending)."""
        result = "done"
        try:
            for state in SEQUENCE:
                if self._state() != "idle":
                    result = "interrupted"
                    return result
                await self._command({"acao": "ring", "state": state, "preview": True})
                await self._sleep(STEP_S)
            if self._state() != "idle":
                result = "interrupted"
                return result
            await self._command({"acao": "ring_countdown", "seconds": COUNTDOWN_S})
            await self._sleep(COUNTDOWN_S + 1)
            return result
        except Exception as e:  # noqa: BLE001 - a preview never takes the agent down
            LOG.warning("ring preview stopped: %s", type(e).__name__)
            result = "error"
            return result
        finally:
            if result != "interrupted":
                try:
                    await self._command({"acao": "ring", "state": None, "preview": True})  # back to the composition
                except Exception:  # noqa: BLE001
                    pass
