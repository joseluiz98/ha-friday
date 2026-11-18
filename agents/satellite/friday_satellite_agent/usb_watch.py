"""USB watchdog of the XVF3800 (spec 2026-11-17-xvf3800-support).

On the Pi 3 the XVF3800's high-speed USB audio hung once on 17/11: capture slowed to ~1/5 of real time, then stopped,
and only a reboot of the Pi brought the USB controller back. This watches the capture rate the voice program publishes
(`captura_taxa`, `captura_idade_s` = monotonic age of the last window) and new kernel `error -71` lines, and asks for a reboot. Only with the XVF3800 (the Jabra
is untouched) and only while the program answers (a program stopped on purpose - switch, OTA, restart - is not a hang).
At most 3 reboots per hour; past that it reports `exhausted` once and waits. After the boot one event tells what was
seen and whether capture came back; the count is kept for the `usb_recoveries` sensor (total_increasing).
"""
from __future__ import annotations

import json
import os
import time

SLOW = 0.9           # fraction of real time
SLOW_WINDOWS = 3     # consecutive 10 s windows
STALE_S = 30.0       # capture window older than this while the program answers = capture stopped
MAX_PER_HOUR = 3
RECOVERY_WAIT_S = 300.0
STATE = os.path.expanduser("~/.friday-agent/usb_recoveries.json")


class UsbWatch:
    def __init__(self, path: str = STATE, now=time.time) -> None:
        self._path, self._now = path, now
        self._slow = 0
        self.reason: str | None = None
        self._boot = now()
        st = self._load()
        self.count: int = st.get("count", 0)
        self._reboots: list[float] = [t for t in st.get("reboots", []) if isinstance(t, (int, float))]
        self._pending: dict | None = st.get("pending")
        self._exhausted_at: float | None = st.get("exhausted_at")

    def _load(self) -> dict:
        try:
            with open(self._path, encoding="utf-8") as f:
                st = json.load(f)
            return st if isinstance(st, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save(self) -> None:
        try:
            os.makedirs(os.path.dirname(self._path), exist_ok=True)
            tmp = self._path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"count": self.count, "reboots": self._reboots, "pending": self._pending,
                           "exhausted_at": self._exhausted_at}, f)
            os.replace(tmp, self._path)
        except OSError:
            pass

    def observe(self, board: str, status: dict | None, kernel_71_new: int) -> str | None:
        """`"reboot"`, `"exhausted"` (once per exhausted hour) or None. `self.reason` says why."""
        if board != "xvf3800" or status is None:
            self._slow = 0
            return None
        now, reason = self._now(), None
        rate, age = status.get("captura_taxa"), status.get("captura_idade_s")
        if kernel_71_new:
            reason = "usb_error_71"
        elif isinstance(age, (int, float)) and age > STALE_S:  # monotonic in the program: NTP steps never count
            reason = "capture_stopped"
        elif isinstance(rate, (int, float)):
            self._slow = self._slow + 1 if rate < SLOW else 0
            if self._slow >= SLOW_WINDOWS:
                reason = "capture_slow"
        if reason is None:
            return None
        self.reason, self._slow = reason, 0
        recent = [t for t in self._reboots if now - t < 3600]
        if len(recent) >= MAX_PER_HOUR:
            if self._exhausted_at is None or now - self._exhausted_at >= 3600:
                self._exhausted_at = now
                self._save()
                return "exhausted"
            return None
        return "reboot"

    def record_reboot(self) -> None:
        """Just before the reboot: the count and the pending event survive it."""
        now = self._now()
        self.count += 1
        self._reboots = [t for t in self._reboots if now - t < 3600] + [now]
        self._pending = {"reason": self.reason, "at": now}
        self._save()

    def after_boot(self, status: dict | None) -> dict | None:
        """The event of the last reboot, once: as soon as capture runs at real time, or after 5 minutes without it."""
        if self._pending is None:
            return None
        rate = status.get("captura_taxa") if status else None
        recovered = isinstance(rate, (int, float)) and rate >= SLOW
        if not recovered and self._now() - self._boot < RECOVERY_WAIT_S:
            return None
        ev = {**self._pending, "recovered": recovered}
        self._pending = None
        self._save()
        return ev


PERIOD_S = 10.0


async def run(board, program, core, kernel_71_count, watch: UsbWatch, reboot, sleep=None,
              period_s: float = PERIOD_S, state: dict | None = None) -> None:
    """The agent's loop. `board` = the board name or a function reading it (re-read every round: a board swap does not
    restart the agent). `kernel_71_count()` = total `error -71` lines of this boot (or None); `reboot()` = the
    privileged reboot; `state` = the agent's hello state (HA reads the count from it on connect). The first -71 reading
    is the baseline: old lines of this boot never reboot. The after-boot event waits until HA is connected."""
    import asyncio
    board_fn = board if callable(board) else (lambda: board)
    if board_fn() != "xvf3800":
        return
    sleep = sleep or asyncio.sleep
    connected = getattr(core, "connected", lambda: True)
    if state is not None:
        state["usb_recoveries"] = watch.count
    await core.push_state({"usb_recoveries": watch.count}, "agent")
    last = await asyncio.to_thread(kernel_71_count)
    pending_event = None
    while True:
        await sleep(period_s)
        status = await program.status()
        pending_event = pending_event or watch.after_boot(status)
        if pending_event and connected():
            await core.push_event("usb_recovery", "rebooted", pending_event)
            pending_event = None
        n = await asyncio.to_thread(kernel_71_count)
        new = n - last if isinstance(n, int) and isinstance(last, int) else 0
        last = n if isinstance(n, int) else last
        action = watch.observe(board_fn(), status, max(new, 0))
        if action == "exhausted":
            await core.push_event("usb_recovery", "exhausted", {"reason": watch.reason})
            await core.push_log(f"USB watchdog: {watch.reason}, but {MAX_PER_HOUR} reboots in the last hour; waiting", {})
        elif action == "reboot":
            watch.record_reboot()
            if state is not None:
                state["usb_recoveries"] = watch.count
            await core.push_state({"usb_recoveries": watch.count}, "agent")
            await core.push_log(f"USB watchdog: {watch.reason}; rebooting the Pi", {})
            await sleep(1.0)  # let the log and the count leave first
            await reboot()
