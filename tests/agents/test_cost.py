"""Gemini cost sensors in BRL (server agent)."""

import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "agents" / "server"))

from friday_server_agent import cost

TODAY = date(2026, 10, 29)


def rec(day, source, usd, model="m", origin="o"):
    return {"day": day, "source": source, "usd": usd, "model": model, "origin": origin}


def run(records, budget=150.0, today=TODAY):
    return cost.compute(records, today, lambda r: r["usd"], lambda r: r["day"], budget)


def test_cost_today_sums_origins_in_brl():
    out = run([rec(TODAY, "voice", 1.0), rec(TODAY, "brain", 0.5), rec(TODAY, "brain", 0.5),
               rec(TODAY, "lucid", 0.1), rec(TODAY, "brain", 2.0, model="gemini-embedding-2"),
               rec(date(2026, 10, 28), "voice", 10.0), rec(TODAY, "voice", None)])
    assert cost.BRL_PER_USD == 7.4
    assert out["cost_today_voice_brl"] == 7.4
    assert out["cost_today_brain_brl"] == 7.4
    assert out["cost_today_lucid_brl"] == 0.74
    assert out["cost_today_embeddings_brl"] == 14.8
    assert out["cost_today_brl"] == pytest.approx(30.34)
    assert out["cost_month_brl"] == pytest.approx(30.34 + 74.0)


def test_projection_scales_by_days_in_month():
    out = run([rec(date(2026, 10, 1), "voice", 10.0)], today=date(2026, 10, 10))
    assert out["cost_month_projection_brl"] == pytest.approx(74.0 / 10 * 31)
    feb = run([rec(date(2026, 2, 1), "voice", 10.0)], today=date(2026, 2, 7))
    assert feb["cost_month_projection_brl"] == pytest.approx(74.0 / 7 * 28)


def test_over_budget_flag():
    recs = [rec(TODAY, "voice", 10.0)]  # 74 BRL
    assert run(recs, budget=150)["cost_month_over_budget"] is False
    assert run(recs, budget=50)["cost_month_over_budget"] is True


def test_budget_persists(tmp_path):
    p = str(tmp_path / "budget-brl")
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
    assert {"cost_today_voice_brl", "cost_today_brain_brl", "cost_today_lucid_brl",
            "cost_today_embeddings_brl", "cost_month_projection_brl"} <= set(d)
