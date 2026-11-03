"""Gemini cost sensors in BRL (server agent). Uses a copy of the VM's cost modules
(tests/agents/fixtures/cost_vm, from /opt/friday/cost) so the sensors are checked against
cost_report's own functions."""

import asyncio
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "agents" / "server"))
FIX = str(Path(__file__).parent / "fixtures" / "cost_vm")
sys.path.insert(0, FIX)

import cost_report  # noqa: E402
import ledger  # noqa: E402
from friday_server_agent import cost  # noqa: E402

TODAY = date(2026, 10, 29)
FLASH_SKU = "4F40-96C8-648E"


def at(d, hour=12):
    return datetime(d.year, d.month, d.day, hour, tzinfo=ledger.BRT)


def make(i, d, source, origin="o", model="gemini-3.8-flash", out=1_000_000):
    return ledger.make_record(at(d), f"id{i}", source, origin, model, None, None, {"output_text": out})


def seed(tmp_path, recs, official=None):
    ledger.append(recs, tmp_path)
    if official:
        (tmp_path / "official").mkdir(exist_ok=True)
        (tmp_path / "official" / "2026-10.json").write_text(json.dumps({"days": official}))
    return tmp_path


def tracker(tmp_path, now=None, budget=150):
    bp = tmp_path / "budget-brl"
    if budget is not None:
        cost.write_budget(budget, str(bp))
    return cost.CostTracker(str(bp), str(tmp_path), FIX, now=now or (lambda: at(TODAY)))


DATA = [make(1, TODAY, "voice"), make(2, TODAY, "brain", out=500_000),
        make(3, TODAY, "lucid", out=100_000), make(4, TODAY, "brain", model="gemini-embedding-2", out=0),
        make(5, date(2026, 10, 20), "voice", out=2_000_000)]
OFFICIAL = {"2026-10-20": [{"sku_id": FLASH_SKU, "cost": 20.0, "credits": 0}]}


def test_sensors_equal_report_functions(tmp_path):
    seed(tmp_path, DATA, OFFICIAL)
    t = tracker(tmp_path)
    ch = t.refresh()
    ctx = cost_report.Ctx(tmp_path, TODAY, None)
    today_recs = [r for r in ctx.records if cost_report.rec_day(r) == TODAY]
    assert ch["cost_today_brl"] == pytest.approx(ctx.total(today_recs), abs=0.005)
    # month through the report's own command
    lines = cost_report.cmd_month(ctx, type("A", (), {"when": "este"})())
    month = next(l for l in lines if l.startswith("Total do mês"))
    brl = float(month.split("R$")[1].strip().replace(".", "").replace(",", "."))
    assert ch["cost_month_brl"] == pytest.approx(brl, abs=0.005)
    assert ch["cost_month_brl"] > ch["cost_today_brl"] + 19  # official day 20 included
    assert ch["cost_month_projection_brl"] == pytest.approx(ch["cost_month_brl"] / 29 * 31, abs=0.01)


def test_factor_falls_back_to_7_4_without_calibration(tmp_path):
    seed(tmp_path, [make(1, TODAY, "voice", out=1_000_000)])  # 3.75 USD list, no official
    ch = tracker(tmp_path).refresh()
    assert cost.BRL_PER_USD == 7.4
    assert ch["cost_today_brl"] == pytest.approx(3.75 * 7.4, abs=0.01)


def test_per_origin_and_over_budget(tmp_path):
    seed(tmp_path, DATA, OFFICIAL)
    ch = tracker(tmp_path, budget=10).refresh()
    assert ch["cost_month_over_budget"] is True
    assert ch["cost_today_voice_brl"] > 0 and ch["cost_today_lucid_brl"] > 0
    assert ch["cost_today_embeddings_brl"] == 0.0
    parts = sum(ch[f"cost_today_{g}_brl"] for g in ("voice", "brain", "lucid", "embeddings"))
    assert parts == pytest.approx(ch["cost_today_brl"], abs=0.02)
    assert tracker(tmp_path, budget=1000).refresh()["cost_month_over_budget"] is False


def test_unknown_source_only_in_total_no_undeclared_key(tmp_path):
    rec = make(1, TODAY, "voice")
    rec["source"] = "mystery"
    seed(tmp_path, [rec, make(2, TODAY, "voice", out=0)])
    ch = tracker(tmp_path).refresh()
    assert ch["cost_today_brl"] > 0
    assert set(ch) <= set(cost.STATE_KEYS)
    assert ch["cost_today_voice_brl"] == 0.0


def test_refresh_diffing_mtime_and_rollover(tmp_path):
    seed(tmp_path, [make(1, TODAY, "voice")])
    clock = {"d": TODAY}
    t = tracker(tmp_path, now=lambda: at(clock["d"]))
    assert set(t.refresh()) == set(cost.STATE_KEYS)
    assert t.refresh() == {}  # unchanged
    seed(tmp_path, [make(2, TODAY, "brain")])  # file grows -> mtime/size change
    p = tmp_path / "usage" / f"{TODAY.isoformat()}.ndjson"
    import os
    os.utime(p, (p.stat().st_atime, p.stat().st_mtime + 5))
    ch = t.refresh()
    assert "cost_today_brain_brl" in ch and "cost_today_voice_brl" not in ch
    clock["d"] = TODAY + timedelta(days=1)  # rollover: today is empty
    ch = t.refresh()
    assert ch["cost_today_brl"] == 0.0


def test_failure_nulls_all_then_recovery_repushes_equal_value(tmp_path):
    seed(tmp_path, [make(1, TODAY, "voice")])
    t = tracker(tmp_path)
    first = t.refresh()
    real_ctx = t._report.Ctx

    def boom(*a, **k):
        raise OSError("disk")
    t._report = type("R", (), {"Ctx": staticmethod(boom)})
    t._key = None
    assert t.refresh() == {} and t.refresh() == {}
    nulled = t.refresh()  # 3rd failure
    assert nulled == dict.fromkeys(cost.STATE_KEYS)
    assert t.refresh() == {}  # no repeat
    t._report = type("R", (), {"Ctx": real_ctx})
    assert t.refresh() == first  # same values come back and are pushed again


def test_budget_persists(tmp_path):
    p = str(tmp_path / "b")
    assert cost.read_budget(p) == 150.0
    cost.write_budget(200, p)
    assert cost.read_budget(p) == 200.0
    with pytest.raises(ValueError):
        cost.write_budget(-1, p)


def test_descriptors():
    d = {x["key"]: x for x in cost.descriptors()}
    assert d["cost_today_brl"]["device_class"] == "monetary" and d["cost_today_brl"]["unit"] == "BRL"
    assert d["cost_today_brl"]["state_class"] == "total"
    assert d["cost_month_over_budget"]["device_class"] == "problem"
    assert set(cost.STATE_KEYS) == set(d)


def run_set(tmp_path, cmd, tr=None):
    tr = tr or tracker(tmp_path)
    seed(tmp_path, [make(1, TODAY, "voice")]) if not (tmp_path / "usage").exists() else None
    state, pushed = {}, []

    async def push(ch):
        pushed.append(ch)
    res = asyncio.run(cost.make_set_handler(tr, state, push)(cmd))
    return res, state, pushed, tr


def test_set_budget_handler(tmp_path):
    res, state, pushed, tr = run_set(tmp_path, {"key": "budget_brl", "value": 5})
    assert res == {"data": {"budget_brl": 5.0}}
    assert cost.read_budget(str(tmp_path / "budget-brl")) == 5.0
    assert pushed[0]["budget_brl"] == 5.0 and pushed[0]["cost_month_over_budget"] is True
    assert state["budget_brl"] == 5.0


def test_set_budget_rejects_bad_input(tmp_path):
    assert run_set(tmp_path, {"key": "budget_brl", "value": -3})[0] == {"ok": False, "error": "invalid_value"}
    assert run_set(tmp_path, {"key": "budget_brl", "value": "abc"})[0] == {"ok": False, "error": "invalid_value"}
    assert run_set(tmp_path, {"key": "other", "value": 1})[0] == {"ok": False, "error": "unknown_key"}


def test_set_budget_survives_refresh_exception(tmp_path):
    tr = tracker(tmp_path)

    def boom():
        raise RuntimeError("x")
    tr.refresh = boom
    res, state, pushed, _ = run_set(tmp_path, {"key": "budget_brl", "value": 80}, tr)
    assert res == {"data": {"budget_brl": 80.0}} and pushed == [{"budget_brl": 80.0}]
