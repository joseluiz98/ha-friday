"""Pure settings spec and validation (no Home Assistant)."""

import pytest

from custom_components.friday import settings as st
from custom_components.friday.protocol import Descriptor


def _caps():
    d = Descriptor
    return [
        d("volume", "number", "Volume", min=0, max=100, step=1),
        d("ducking", "switch", "Ducking", hidden=True),
        d("ducking_level", "number", "Level", min=1, max=25, step=1, hidden=True),
        d("ducking_restore", "number", "Restore", min=0, max=10, step=0.5, hidden=True),
        d("continuous_speech_limit", "number", "Cutoff", min=0, max=120, step=5, hidden=True),
        d("wakeword_friday_mode", "select", "M", options=["active", "shadow", "off"], hidden=True),
        d("wakeword_friday_sensitivity", "number", "T", min=0.05, max=0.99, step=0.01, hidden=True),
    ]


def code(fn, *args):
    with pytest.raises(st.SettingError) as err:
        fn(*args)
    return err.value.code


def test_validate_general_ranges():
    assert st.validate_general("end_of_speech_pause", 1.2) == 1.2
    v = st.validate_general("reply_wait", 9.0)
    assert v == 9 and isinstance(v, int)
    assert code(st.validate_general, "follow_up_window", 0) == "out_of_range"
    assert code(st.validate_general, "follow_up_window", 31) == "out_of_range"
    assert code(st.validate_general, "end_of_speech_pause", 0.4) == "out_of_range"
    assert code(st.validate_general, "no_follow_up_while_music", "yes") == "bad_type"
    assert st.validate_general("no_follow_up_while_music", False) is False
    assert code(st.validate_general, "close_mic_awaiting_reply", True) == "unknown_key"


def test_validate_advanced_from_descriptors():
    caps = _caps()
    assert st.validate_advanced(caps, "continuous_speech_limit", 0) == 0
    assert code(st.validate_advanced, caps, "continuous_speech_limit", 3) == "out_of_range"
    v = st.validate_advanced(caps, "continuous_speech_limit", 30.0)
    assert v == 30 and isinstance(v, int)
    assert code(st.validate_advanced, caps, "wakeword_friday_mode", "sleep") == "out_of_range"
    assert st.validate_advanced(caps, "wakeword_friday_mode", "shadow") == "shadow"
    assert code(st.validate_advanced, caps, "wakeword_friday_sensitivity", 1.0) == "out_of_range"
    assert st.validate_advanced(caps, "wakeword_friday_sensitivity", 0.35) == 0.35
    assert code(st.validate_advanced, caps, "ducking_level", 20) == "unknown_key"  # 11/11: on the device page
    assert st.validate_advanced(caps, "ducking_restore", 1.5) == 1.5
    assert code(st.validate_advanced, caps, "wakeword_other_mode", "active") == "unknown_key"
    assert code(st.validate_advanced, caps, "volume", 5) == "unknown_key"
    assert code(st.validate_advanced, caps, "ducking", True) == "unknown_key"


def test_advanced_keys_in_descriptor_order():
    assert st.advanced_keys(_caps()) == [
        "ducking_restore", "continuous_speech_limit",
        "wakeword_friday_mode", "wakeword_friday_sensitivity"]
    assert st.advanced_keys([Descriptor("volume", "number", "V")]) == []


def test_check_wakewords():
    assert code(st.check_wakewords, {"friday": "off", "sexta_feira": "shadow"}) == "one_active_word"
    st.check_wakewords({"friday": "active", "sexta_feira": "shadow"})


def test_general_plan():
    general = {"reply_wait": 8, "follow_up_window": 7, "end_of_speech_pause": 1.2}
    keys = {"reply_wait", "follow_up_window", "end_of_speech_pause"}
    state = {"reply_wait": 8, "follow_up_window": 5, "end_of_speech_pause": 0.8}
    assert st.general_plan(general, state, keys) == {"follow_up_window": 7, "end_of_speech_pause": 1.2}
    assert st.general_plan(general, dict(state, follow_up_window=0), keys) == {"end_of_speech_pause": 1.2}
    assert st.general_plan(general, state, {"reply_wait"}) == {}
