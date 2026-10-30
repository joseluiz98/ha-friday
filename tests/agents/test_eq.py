"""Simple 3-band equalizer: capability detection, band mapping, async apply."""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "agents" / "satellite"))

from friday_satellite_agent import eq  # noqa: E402

ALL = "".join(f"Simple mixer control '{n}',0\n" for n in eq.CONTROLS)


def make_runner(present, log, fail=()):
    def run(argv):
        log.append(argv)
        if "scontrols" in argv:
            return (0, ALL) if present else (1, "")
        return (1, "") if any(f in a for a in argv for f in fail) else (0, "")
    return run


def new(tmp_path, present=True, log=None, fail=()):
    e = eq.Equalizer(runner=make_runner(present, [] if log is None else log, fail),
                     state_path=str(tmp_path / "eq.json"))
    e.detect()
    return e


def test_no_capability_when_plugin_missing(tmp_path):
    log = []
    e = new(tmp_path, False, log)
    assert e.available is False and e.descriptors() == [] and e.state() == {}
    assert asyncio.run(e.apply({"eq_bass": 3}))["ok"] is False
    assert not any("cset" in a for a in log)


def test_incomplete_controls_not_declared(tmp_path):
    e = eq.Equalizer(runner=lambda a: (0, "Simple mixer control '00. 31 Hz',0\n"), state_path=str(tmp_path / "x"))
    assert e.detect() is False and e.descriptors() == []


def test_descriptors_when_present(tmp_path):
    d = {x["key"]: x for x in new(tmp_path).descriptors()}
    assert set(d) == {"eq_bass", "eq_mid", "eq_treble"}
    assert all((x["min"], x["max"], x["step"], x["category"]) == (-12, 12, 1, "config") for x in d.values())


def test_flat_is_default(tmp_path):
    assert new(tmp_path).state() == {"eq_bass": 0, "eq_mid": 0, "eq_treble": 0}
    assert set(eq.control_values(0, 0, 0).values()) == {66}


def test_band_mapping():
    v = eq.control_values(bass=12, mid=0, treble=-12)
    assert v == {"00. 31 Hz": 83, "01. 63 Hz": 83, "02. 125 Hz": 83,
                 "03. 250 Hz": 66, "04. 500 Hz": 66, "05. 1 kHz": 66, "06. 2 kHz": 66,
                 "07. 4 kHz": 49, "08. 8 kHz": 49, "09. 16 kHz": 49}


def test_apply_clamps_and_persists(tmp_path):
    log = []
    e = new(tmp_path, True, log)
    assert asyncio.run(e.apply({"eq_mid": 99})) == {"ok": True}
    assert e.state()["eq_mid"] == 12
    assert len([a for a in log if "cset" in a]) == 4  # only the mid band is written
    e2 = new(tmp_path)
    assert e2.state()["eq_mid"] == 12


def test_partial_failure_reports_reality(tmp_path):
    e = new(tmp_path, True, fail=("16 kHz",))
    r = asyncio.run(e.apply({"eq_bass": 5, "eq_treble": 5}))
    assert r == {"ok": False, "error": "command_failed"}
    assert e.state() == {"eq_bass": 5, "eq_mid": 0, "eq_treble": 0}


def test_startup_reapplies_persisted(tmp_path):
    (tmp_path / "eq.json").write_text('{"eq_bass": 4, "eq_mid": 0, "eq_treble": 0}')
    log = []
    e = new(tmp_path, True, log)
    assert asyncio.run(e.startup()) == {"ok": True}
    assert len([a for a in log if "cset" in a]) == 10


def test_rapid_sets_last_wins(tmp_path):
    log = []
    e = new(tmp_path, True, log)

    async def go():
        await asyncio.gather(*(e.apply({"eq_bass": n}) for n in (1, 2, 3)))
    asyncio.run(go())
    assert e.state()["eq_bass"] == 3
