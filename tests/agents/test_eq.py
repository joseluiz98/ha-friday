"""Simple 3-band equalizer: capability detection and band mapping to Eq10."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "agents" / "satellite"))

from friday_satellite_agent import eq  # noqa: E402


def make_runner(present, log):
    def run(argv):
        log.append(argv)
        return (0, "Simple mixer control '00. 31 Hz',0\n") if present else (1, "")
    return run


def test_no_capability_when_plugin_missing():
    log = []
    e = eq.Equalizer(runner=make_runner(False, log))
    assert e.available is False
    assert e.descriptors() == []
    assert e.apply({"eq_bass": 3}) is False
    assert all(a[:3] == ["amixer", "-D", "equal"] and "cset" not in a for a in log)


def test_descriptors_when_present():
    e = eq.Equalizer(runner=make_runner(True, []))
    d = {x["key"]: x for x in e.descriptors()}
    assert set(d) == {"eq_bass", "eq_mid", "eq_treble"}
    for x in d.values():
        assert (x["platform"], x["min"], x["max"], x["step"], x["category"]) == ("number", -12, 12, 1, "config")
        assert x["unit"] == "dB"


def test_flat_is_default():
    log = []
    e = eq.Equalizer(runner=make_runner(True, log))
    assert e.state() == {"eq_bass": 0, "eq_mid": 0, "eq_treble": 0}
    assert set(eq.control_values(0, 0, 0).values()) == {eq.FLAT}


def test_band_mapping():
    v = eq.control_values(bass=12, mid=0, treble=-12)
    names = list(v)
    assert len(names) == 10
    assert [v[n] for n in names[:3]] == [round(eq.FLAT + eq.STEP * 12)] * 3   # 31, 63, 125 Hz
    assert [v[n] for n in names[3:7]] == [eq.FLAT] * 4                  # 250 Hz - 2 kHz
    assert [v[n] for n in names[7:]] == [round(eq.FLAT - eq.STEP * 12)] * 3   # 4, 8, 16 kHz


def test_apply_writes_and_clamps():
    log = []
    e = eq.Equalizer(runner=make_runner(True, log))
    assert e.apply({"eq_mid": 99}) is True
    assert e.state()["eq_mid"] == 12
    cset = [a for a in log if "cset" in a]
    assert len(cset) == 10
