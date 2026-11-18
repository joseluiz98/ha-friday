"""Satellite agent: the audio board (Jabra Speak 410 or XVF3800) shown in HA and the XVF-only settings
(spec 2026-11-17 xvf3800-support, Task 7)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "agents" / "satellite"))

from friday_satellite_agent import capabilities as cap  # noqa: E402

CFG = {"volume": 6, "nao_perturbe": False, "volume_alertas": 6}


def test_model_follows_audio_board():
    assert cap.model_for_board("xvf3800") == "Raspberry Pi 3 + XVF3800"
    assert cap.model_for_board("jabra") == "Raspberry Pi 3 + Jabra Speak 410"
    assert cap.model_for_board("none") == "Raspberry Pi 3"
    assert cap.model_for_board(None) == "Raspberry Pi 3 + Jabra Speak 410"  # file absent: satellites older than the select step


def test_read_board(tmp_path):
    f = tmp_path / "audio_board"
    assert cap.read_board(str(f)) is None  # absent
    f.write_text("xvf3800\n")
    assert cap.read_board(str(f)) == "xvf3800"
    f.write_text("none\n")
    assert cap.read_board(str(f)) == "none"
    f.write_text("other\n")
    assert cap.read_board(str(f)) is None


def _by_key(board):
    return {c["key"]: c for c in cap.controls_from_config(CFG, [], board=board)}


def test_delay_setting_only_on_xvf():
    xvf = _by_key("xvf3800")
    d = xvf["xvf_sys_delay"]
    assert (d["platform"], d["name"], d["min"], d["max"], d["step"], d["category"]) == (
        "number", "XVF3800 delay", 0, 256, 1, "config")
    b = xvf["beam_lock"]
    assert (b["platform"], b["name"], b["category"]) == ("switch", "Beam lock", "config")
    for board in ("jabra", "none", None):
        keys = _by_key(board)
        assert "xvf_sys_delay" not in keys and "beam_lock" not in keys, board


def test_xvf_descriptors_do_not_change_the_default_set():
    """Callers that pass no board (older code, contract tests) see exactly the Jabra set."""
    assert {c["key"] for c in cap.controls_from_config(CFG, [])} == {c["key"] for c in cap.controls_from_config(CFG, [], board="jabra")}


def test_delay_roundtrip_config():
    m = cap.Mapper(board="xvf3800")
    assert m.state({}, [], None)["xvf_sys_delay"] == 12  # factory default
    assert m.state({"xvf_sys_delay": 20}, [], None)["xvf_sys_delay"] == 20
    # HA number entities send 20.0; the program accepts only int
    plan = m.to_program({}, "xvf_sys_delay", 20.0)
    assert plan == {"config": {"xvf_sys_delay": 20}} and isinstance(plan["config"]["xvf_sys_delay"], int)


def test_beam_lock_roundtrip_config():
    m = cap.Mapper(board="xvf3800")
    assert m.state({}, [], None)["beam_lock"] is True  # on by default
    assert m.state({"beam_lock": False}, [], None)["beam_lock"] is False
    assert m.to_program({}, "beam_lock", False) == {"config": {"beam_lock": False}}
    assert m.to_program({}, "beam_lock", True) == {"config": {"beam_lock": True}}


def test_xvf_state_keys_only_on_xvf():
    for board in ("jabra", None):
        st = cap.Mapper(board=board).state({"xvf_sys_delay": 20}, [], None)
        assert "xvf_sys_delay" not in st and "beam_lock" not in st
    down = cap.Mapper(board="xvf3800")
    down.state({}, [], None)
    assert down.down_state()["xvf_sys_delay"] is None and down.down_state()["beam_lock"] is None  # program stopped: unknown
