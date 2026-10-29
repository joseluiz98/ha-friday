"""Gemini cost sensors in BRL for the server agent.

Reuses the VM's cost instrumentation (spec 2026-10-23, /opt/friday/cost: ledger.read for the
usage records, pricing.list_cost_usd for the list-price maths) instead of recomputing it.
Those modules are imported at runtime from COST_DIR; the pure functions below take the
records and a `cost_usd(record)` callable, so they are testable without the VM.

The sensors AGREE with cost_report.py: they use its Ctx (bill-calibrated R$/US$ factor per
month, official figures for closed days) and fall back to BRL_PER_USD = 7.4 (list price x 7.4,
memory "Cobranca do Gemini API"; = DEFAULT_FACTOR in official.py) only when no calibrated
factor exists (Ctx.factor does that itself).
"""

from __future__ import annotations

import asyncio
import calendar
import logging
import os
import sys
import threading
from datetime import date, datetime, timedelta
from pathlib import Path

LOG = logging.getLogger(__name__)

BRL_PER_USD = 7.4
COST_DIR = os.environ.get("FRIDAY_COST_DIR", "/opt/friday/cost")
STATE_DIR = os.environ.get("FRIDAY_COST_STATE", "/srv/friday/state/cost")
BUDGET_PATH = "/srv/friday/state/budget-brl"
DEFAULT_BUDGET = 150.0
SOURCES = ("voice", "brain", "lucid")


def _brl(key, name, **kw):
    return {"key": key, "platform": "sensor", "name": name, "device_class": "monetary",
            "unit": "BRL", "state_class": "total", "icon": "mdi:cash", **kw}


def descriptors() -> list[dict]:
    out = [_brl("cost_today_brl", "Gemini cost today"),
           _brl("cost_month_brl", "Gemini cost this month"),
           _brl("cost_month_projection_brl", "Gemini cost month projection")]
    for src in (*SOURCES, "embeddings"):
        out.append(_brl(f"cost_today_{src}_brl", f"Gemini cost today ({src})", category="diagnostic"))
    out.append({"key": "cost_month_over_budget", "platform": "binary_sensor",
                "name": "Gemini cost over budget", "device_class": "problem"})
    return out


BUDGET_DESCRIPTOR = {"key": "budget_brl", "platform": "number", "name": "Gemini monthly budget",
                     "category": "config", "unit": "BRL", "min": 0, "max": 10000, "step": 10,
                     "mode": "box", "icon": "mdi:cash-lock"}


def read_budget(path: str = BUDGET_PATH) -> float:
    try:
        with open(path, encoding="utf-8") as f:
            v = float(f.read().strip())
        return v if v >= 0 else DEFAULT_BUDGET
    except (OSError, ValueError):
        return DEFAULT_BUDGET


def write_budget(value: float, path: str = BUDGET_PATH) -> float:
    value = float(value)
    if value < 0 or value != value:
        raise ValueError("budget must be >= 0")
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(f"{value}\n")
    os.replace(tmp, path)
    return value


def _group(record: dict) -> str:
    """Embeddings are recognised by model/origin name; everything else by its ledger source."""
    if "embed" in f"{record.get('model', '')}{record.get('origin', '')}".lower():
        return "embeddings"
    return record.get("source", "")


STATE_KEYS = ["cost_today_brl", "cost_month_brl", "cost_month_projection_brl", "cost_month_over_budget",
              *(f"cost_today_{g}_brl" for g in (*SOURCES, "embeddings"))]
FAIL_LIMIT = 3
_warned_sources: set = set()


def compute(ctx, today: date, budget: float, day_of) -> dict:
    """ctx: a cost_report.Ctx (or look-alike: records, official, est(), total()).
    Unknown ledger sources count only in the totals (logged once at debug)."""
    today_recs = [r for r in ctx.records if day_of(r) == today]
    groups = dict.fromkeys((*SOURCES, "embeddings"), 0.0)
    for r in today_recs:
        g = _group(r)
        v = ctx.est(r)
        if g in groups:
            groups[g] += v or 0.0
        elif g not in _warned_sources:
            _warned_sources.add(g)
            LOG.debug("cost: unknown ledger source %r counted in totals only", g)
    # Same method as cost_report.cmd_month (inline there, so it cannot be imported): official
    # figure for closed days, estimate otherwise.
    first = today.replace(day=1)
    by_day: dict = {}
    for r in ctx.records:
        d = day_of(r)
        if first <= d <= today:
            by_day.setdefault(d, []).append(r)
    month, d = 0.0, first
    while d <= today:
        o = ctx.official.get(d)
        if o and o["closed"]:
            month += o["total_brl"]
        elif by_day.get(d):
            month += ctx.total(by_day[d], track_unpriced=False)
        d += timedelta(days=1)
    days_in_month = calendar.monthrange(today.year, today.month)[1]
    r2 = lambda x: round(x, 2)  # noqa: E731
    out = {"cost_today_brl": r2(ctx.total(today_recs, track_unpriced=False)),
           "cost_month_brl": r2(month),
           "cost_month_projection_brl": r2(month / today.day * days_in_month),
           "cost_month_over_budget": month > budget}
    for g, v in groups.items():
        out[f"cost_today_{g}_brl"] = r2(v)
    return out


class CostTracker:
    """Recomputes when today's usage file changes (mtime), the date rolls over or the budget
    changes. Thread-safe (the sampling loop and the set handler run in worker threads)."""

    def __init__(self, budget_path: str = BUDGET_PATH, state_dir: str = STATE_DIR,
                 cost_dir: str = COST_DIR, now=None) -> None:
        self.budget_path, self.state_dir, self.cost_dir = budget_path, state_dir, cost_dir
        self.budget = read_budget(budget_path)
        self._lock = threading.Lock()
        if cost_dir not in sys.path:
            sys.path.insert(0, cost_dir)
        import cost_report  # noqa: PLC0415 - /opt/friday/cost
        import ledger  # noqa: PLC0415
        self._report, self._ledger = cost_report, ledger
        self._now = now or (lambda: datetime.now(ledger.BRT))
        self._key = None
        self._failures = 0
        self.last: dict = {}

    def set_budget(self, value) -> dict:
        with self._lock:
            self.budget = write_budget(value, self.budget_path)
        return {"budget_brl": self.budget}

    def _day_of(self, r):
        return datetime.fromisoformat(r["ts"]).astimezone(self._ledger.BRT).date()

    def refresh(self) -> dict:
        """Returns the changed state keys. After FAIL_LIMIT consecutive failures every cost key
        goes to None (and is re-pushed when a value returns, even an equal one). Never raises."""
        with self._lock:
            try:
                today = self._now().date()
                f = Path(self.state_dir) / "usage" / f"{today.isoformat()}.ndjson"
                try:
                    mtime = f.stat().st_mtime
                except OSError:
                    mtime = None
                key = (today, mtime, self.budget)
                if key == self._key:
                    return {}
                ctx = self._report.Ctx(self.state_dir, today, None)
                state = compute(ctx, today, self.budget, self._day_of)
            except Exception as err:  # noqa: BLE001
                self._failures += 1
                LOG.warning("cost recompute failed: %s", type(err).__name__)
                if self._failures == FAIL_LIMIT:
                    self.last = dict.fromkeys(STATE_KEYS)
                    self._key = None
                    return dict(self.last)
                return {}
            self._failures = 0
            self._key = key
            changes = {k: v for k, v in state.items() if self.last.get(k) != v}
            self.last = state
            return changes


def make_set_handler(tracker: CostTracker, state: dict, push):
    """`push` is an async callable(changes: dict)."""
    async def handle(cmd: dict) -> dict:
        if cmd.get("key") != "budget_brl":
            return {"ok": False, "error": "unknown_key"}
        try:
            changes = await asyncio.to_thread(tracker.set_budget, cmd.get("value"))
        except (ValueError, TypeError):
            return {"ok": False, "error": "invalid_value"}
        except OSError:
            return {"ok": False, "error": "write_failed"}
        try:
            changes.update(await asyncio.to_thread(tracker.refresh))
        except Exception as err:  # noqa: BLE001 - budget is saved; the next sample catches up
            LOG.warning("cost refresh after budget change failed: %s", type(err).__name__)
        state.update(changes)
        await push(changes)
        return {"data": {"budget_brl": tracker.budget}}
    return handle
