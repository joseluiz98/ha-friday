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
    assert st.GENERAL_SPECS["only_wake_word_interrupts"] == {"type": "bool"}
    assert code(st.validate_general, "only_wake_word_interrupts", "yes") == "bad_type"
    assert st.validate_general("only_wake_word_interrupts", False) is False
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


def test_update_channel_is_an_advanced_setting():
    """14/11: the update channel lives in the satellite's Advanced step, not on the device page."""
    caps = _caps() + [Descriptor("ota_channel", "select", "Update channel", options=["stable", "alpha"], hidden=True)]
    assert st.advanced_keys(caps)[-1] == "ota_channel"
    assert st.validate_advanced(caps, "ota_channel", "alpha") == "alpha"
    assert code(st.validate_advanced, caps, "ota_channel", "beta") == "out_of_range"


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


def test_recording_switches_are_advanced_settings():
    """18/11: record_ambient and record_activations live in the satellite's Advanced step."""
    d = Descriptor
    caps = _caps() + [d("record_ambient", "switch", "Record ambient audio", hidden=True),
                      d("record_activations", "switch", "Record activations", hidden=True)]
    keys = st.advanced_keys(caps)
    assert "record_ambient" in keys and "record_activations" in keys
    assert st.validate_advanced(caps, "record_ambient", True) is True
    assert st.validate_advanced(caps, "record_activations", False) is False
    assert code(st.validate_advanced, caps, "record_ambient", "on") == "bad_type"
    assert code(st.validate_advanced, caps, "record_activations", 1) == "bad_type"
    # an old agent that does not announce them: unknown
    assert code(st.validate_advanced, _caps(), "record_ambient", True) == "unknown_key"


def test_two_beam_keys_and_texts_in_all_files():
    import json, pathlib
    keys = ["two_beam_listening", "tv_azimuth", "tv_elevation", "caller_margin", "tv_margin",
            "caller_silence", "turn_ceiling"]
    assert all(k in st.ADVANCED_FIXED for k in keys)
    base = pathlib.Path(st.__file__).parent
    for f in ("strings.json", "translations/en.json", "translations/pt-BR.json"):
        adv = json.loads((base / f).read_text())["config_subentries"]["satellite"]["step"]["advanced"]
        for k in keys:
            assert adv["data"][k] and 0 < len(adv["data_description"][k]) <= 220
