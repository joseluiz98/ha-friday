import datetime as dt
from zoneinfo import ZoneInfo
from friday_agent_core.ota import retry as r

SP = ZoneInfo("America/Sao_Paulo")
ts = lambda h, mi, d=10: dt.datetime(2026, 11, d, h, mi, tzinfo=SP).timestamp()

def test_backoff_then_exhaust():
    a = r.reset("v0.3.0")
    for i, d in enumerate((900, 3600, 21600, 86400)):
        r.record_failure(a, "download", "timeout", now=100.0)
        assert a.next_at == 100.0 + d and not a.exhausted
    r.record_failure(a, "health", "no_program", now=100.0)
    assert a.exhausted and a.failures == 5 and a.last_stage == "health"
    assert not r.may_try(a, "v0.3.0", now=10**12)

def test_new_version_resets():
    a = r.reset("v0.3.0"); a.exhausted = True
    assert r.may_try(a, "v0.4.0", now=0) and r.may_try(None, "v0.3.0", now=0)

def test_waits_until_next_at():
    a = r.record_failure(r.reset("v1.0.0"), "stage", "disk", now=0)
    assert not r.may_try(a, "v1.0.0", now=899) and r.may_try(a, "v1.0.0", now=900)

def test_roundtrip():
    a = r.record_failure(r.reset("v1.0.0"), "s", "e" * 500, now=1)
    b = r.Attempts.from_dict(a.to_dict())
    assert b == a and len(b.last_error) == 200

def test_push_window():
    assert r.push_at(ts(9, 0), SP) == ts(9, 0)
    assert r.push_at(ts(23, 15), SP) == ts(7, 30, d=11)
    assert r.push_at(ts(3, 0), SP) == ts(7, 30)
    assert r.push_at(ts(22, 0), SP) == ts(7, 30, d=11)
