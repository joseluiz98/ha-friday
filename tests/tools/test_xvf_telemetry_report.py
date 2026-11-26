import importlib.util
import json
import pathlib

_P = pathlib.Path(__file__).resolve().parents[2] / "tools" / "satellite_measurements" / "xvf_telemetry_report.py"
_spec = importlib.util.spec_from_file_location("xvf_telemetry_report", _P)
rep = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rep)

ON = [[{"state": "off", "last_changed": "2026-11-20T10:00:00+00:00"},
       {"state": "on", "last_changed": "2026-11-20T12:00:00+00:00"},
       {"state": "off", "last_changed": "2026-11-20T14:00:00+00:00"}]]
T_ON = rep.parse_ts("2026-11-20T13:00:00+00:00")
T_OFF = rep.parse_ts("2026-11-20T11:00:00+00:00")


def rec(t, az, e, fx, sel=None):
    return {"t": t, "az": az, "e": e, "sel": sel, "doa": [0, 0], "fx": fx}


def test_tv_azimuth_from_free_beam_when_tv_on():
    iv = rep.tv_on_intervals(ON)
    recs = [rec(T_ON + i, [10, 20, 358 + (i % 5), 0], [1, 1, 0.5, 0], 0) for i in range(20)]
    recs += [rec(T_OFF, [0, 0, 180, 0], [1, 1, 0.5, 0], 0)]          # TV off: ignored
    r = rep.telemetry_report(recs, iv)
    assert r["tv"]["n"] == 20
    assert r["tv"]["median_deg"] in (0.0, 359.0, 360.0 % 360)
    assert r["tv"]["spread_p90_deg"] <= 4


def test_autoselect_follow_fraction():
    recs = [rec(1, [90, 0, 200, 202], [2, 1, 1, 0], 1),   # follows free: counts
            rec(2, [90, 0, 200, 91], [2, 1, 1, 0], 1),    # follows caller
            rec(3, [90, 0, 200, 150], [2, 1, 1, 0], 1),
            rec(4, [90, 0, 200, 199], [2, 1, 1, 0], 1)]
    r = rep.telemetry_report(recs, [])
    f = r["autoselect_follows_free_in_fixed"]
    assert f["n"] == 4 and f["fraction"] == 0.5
    assert r["callers"]["median_deg"] == 90.0
    assert r["energy"]["n"] == 4


def test_ignores_records_without_energy():
    iv = rep.tv_on_intervals(ON)
    recs = [rec(T_ON, [0, 0, 50, 0], [1, 1, 0, 0], 0), rec(T_ON + 1, [0, 0, 50, 0], None, 0),
            rec(5, [10, 0, 0, 0], [0, 1, 1, 0], 1), rec(6, [10, 0, 0, 0], [1, 0, 1, 0], 1)]
    r = rep.telemetry_report(recs, iv)
    assert r["tv"]["n"] == 0 and r["tv"]["median_deg"] is None
    assert r["energy"]["n"] == 0


def test_journal_summary_counts():
    lines = [json.dumps(x) for x in [
        {"two_beam_mode": {"mode": "pi", "reason": "locked"}},
        {"two_beam_mode": {"mode": "pi", "reason": "locked"}},
        {"two_beam_mode": {"mode": "v046", "reason": "no_wake_word"}},
        {"two_beam_turn_ceiling": {"turn_s": 60.0}},
        {"two_beam_dropped_candidate": {"s": 0.6, "ratio_db_median": -3.0}},
        {"two_beam_dropped_candidate": {"s": 0.8, "ratio_db_median": -5.0}},
        {"two_beam_summary": {"by": {"pause": 3, "ceiling": 1}, "reaims": 2, "energy_stale_s": 1.5, "release": "closed"}},
        {"two_beam_summary": {"by": {"pause": 1, "caller_voice": 2}, "reaims": 0, "energy_stale_s": 0.5, "release": "error"}},
    ]] + ["not json", "{}"]
    s = rep.journal_summary(lines)
    assert s["conversations_by_reason"] == {"locked": 2, "no_wake_word": 1}
    assert s["turn_ends_by"] == {"pause": 4, "ceiling": 1, "caller_voice": 2}
    assert s["turn_ceiling_count"] == 1
    assert s["dropped_candidates"] == {"n": 2, "median_ratio_db": -4.0}
    assert s["reaims_per_conversation"] == 1.0
    assert s["energy_stale_s_total"] == 2.0
    assert s["releases_by_reason"] == {"closed": 1, "error": 1}
