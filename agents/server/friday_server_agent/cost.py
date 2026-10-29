"""Gemini cost sensors in BRL for the server agent.

Reuses the VM's cost instrumentation (spec 2026-10-23, /opt/friday/cost: ledger.read for the
usage records, pricing.list_cost_usd for the list-price maths) instead of recomputing it.
Those modules are imported at runtime from COST_DIR; the pure functions below take the
records and a `cost_usd(record)` callable, so they are testable without the VM.

BRL_PER_USD: list price x 7.4 (memory "Cobranca do Gemini API"; same as DEFAULT_FACTOR in
/opt/friday/cost/official.py). cost_report.py may show a slightly different factor (it
derives one from the official monthly bill when available); the sensors deliberately use the
documented constant.
"""

from __future__ import annotations

import calendar
import os
import sys
from datetime import date, datetime
from pathlib import Path

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


def compute(records, today: date, cost_usd, day_of, budget: float) -> dict:
    """records: ledger records; cost_usd(record) -> USD or None (unpriced, skipped);
    day_of(record) -> local date."""
    today_by = dict.fromkeys((*SOURCES, "embeddings"), 0.0)
    month = 0.0
    for r in records:
        d = day_of(r)
        if (d.year, d.month) != (today.year, today.month) or d > today:
            continue
        usd = cost_usd(r)
        if usd is None:
            continue
        brl = usd * BRL_PER_USD
        month += brl
        if d == today:
            today_by[_group(r)] = today_by.get(_group(r), 0.0) + brl
    days_in_month = calendar.monthrange(today.year, today.month)[1]
    projection = month / today.day * days_in_month
    r2 = lambda x: round(x, 2)  # noqa: E731
    out = {"cost_today_brl": r2(sum(today_by.values())), "cost_month_brl": r2(month),
           "cost_month_projection_brl": r2(projection),
           "cost_month_over_budget": month > budget}
    for src, v in today_by.items():
        out[f"cost_today_{src}_brl"] = r2(v)
    return out


class CostTracker:
    """Recomputes when today's usage file changes (mtime) or the date rolls over."""

    def __init__(self, budget_path: str = BUDGET_PATH, state_dir: str = STATE_DIR,
                 cost_dir: str = COST_DIR, now=None) -> None:
        self.budget_path, self.state_dir, self.cost_dir = budget_path, state_dir, cost_dir
        self.budget = read_budget(budget_path)
        self._now = now or (lambda: datetime.now(self._ledger.BRT))
        self._key = None
        self.last: dict = {}
        if cost_dir not in sys.path:
            sys.path.insert(0, cost_dir)
        import ledger  # noqa: PLC0415 - /opt/friday/cost
        import pricing  # noqa: PLC0415
        self._ledger, self._pricing = ledger, pricing
        self._prices = None

    def set_budget(self, value: float) -> dict:
        self.budget = write_budget(value, self.budget_path)
        self._key = None
        return {"budget_brl": self.budget}

    def refresh(self) -> dict:
        """Returns the changed state keys (all of them when the inputs changed)."""
        today = self._now().date()
        f = Path(self.state_dir) / "usage" / f"{today.isoformat()}.ndjson"
        try:
            mtime = f.stat().st_mtime
        except OSError:
            mtime = None
        key = (today, mtime, self.budget)
        if key == self._key:
            return {}
        if self._prices is None:
            self._prices = self._pricing.load_prices()
        records, _ = self._ledger.read(today.replace(day=1), today, self.state_dir)
        state = compute(records, today, lambda r: self._pricing.list_cost_usd(r, self._prices),
                        lambda r: datetime.fromisoformat(r["ts"]).astimezone(self._ledger.BRT).date(),
                        self.budget)
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
            changes = tracker.set_budget(cmd.get("value"))
        except (OSError, ValueError, TypeError):
            return {"ok": False, "error": "invalid_value"}
        changes.update(tracker.refresh())
        state.update(changes)
        await push(changes)
        return {"data": {"budget_brl": tracker.budget}}
    return handle
