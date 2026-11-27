"""ALSA configs per audio board (spec 2026-11-17 xvf3800-support; spec 2026-11-27 fine-volume). Both boards expose
the same PCM names to the program, the Sendspin player and the shairport, and `fine_vol` (softvol `Fine`) is the
only client of the dmix."""
import re
from pathlib import Path

AUDIO = Path(__file__).resolve().parents[2] / "satellite" / "audio"
COMMON = ("pcm.voice", "pcm.music", "pcm.music_vol", "pcm.jabra_entrada", 'name "Music"', "pcm.fine_vol", 'name "Fine"')


def test_both_confs_define_common_pcms():
    for name in ("jabra.conf", "xvf3800.conf"):
        text = (AUDIO / name).read_text(encoding="utf-8")
        for needle in COMMON:
            assert needle in text, f"{name} lacks {needle}"


def test_xvf_conf_uses_channel_1_and_speexrate():
    text = (AUDIO / "xvf3800.conf").read_text(encoding="utf-8")
    assert "hw:CARD=Array" in text
    assert "ttable.0.1 1" in text
    assert 'defaults.pcm.rate_converter "speexrate_medium"' in text
    assert "pcm.mono_out" in text and "type route" in text
    assert text.count("ttable.0.0 0.5") == 1 and text.count("ttable.1.1 0.5") == 1


def test_xvf_conf_offers_both_channels_for_the_conversation():
    """18/11 (owner, option b): the program reads channel 0 (conversation) and channel 1 (wake word) in one capture."""
    import re
    text = (AUDIO / "xvf3800.conf").read_text(encoding="utf-8")
    block = re.search(r"pcm\.xvf_dois \{(.*?)\n\}", text, re.S)
    assert block, "xvf3800.conf lacks pcm.xvf_dois"
    body = block.group(1)
    assert "type plug" in body and 'pcm "xvf_in"' in body and "channels 2" in body and "ttable" not in body


def test_xvf_conf_controls_live_on_the_xvf_card():
    text = (AUDIO / "xvf3800.conf").read_text(encoding="utf-8")
    assert "card Array" in text and "card USB" not in text


def _block(text, name):
    found = re.search(r"pcm\.%s \{(.*?)\n\}" % name, text, re.S)
    assert found, name
    return found.group(1)


def test_fine_softvol_is_the_only_client_of_the_dmix():
    for conf, card in (("jabra.conf", "USB"), ("xvf3800.conf", "Array")):
        text = (AUDIO / conf).read_text(encoding="utf-8")
        body = _block(text, "fine_vol")
        for needle in ("type softvol", 'slave.pcm "mixer_out"', 'name "Fine"', f"card {card}", "min_dB -6.0",
                       "max_dB 0.0", "resolution 121"):
            assert needle in body, f"{conf} fine_vol lacks {needle}"
        assert text.count('slave.pcm "mixer_out"') == 1, conf


def test_voice_and_music_pass_through_fine():
    jabra = (AUDIO / "jabra.conf").read_text(encoding="utf-8")
    for pcm in ("voice", "music_vol"):
        assert 'slave.pcm "fine_vol"' in _block(jabra, pcm), pcm
    xvf = (AUDIO / "xvf3800.conf").read_text(encoding="utf-8")
    assert 'slave.pcm "fine_vol"' in _block(xvf, "mono_out")
    for pcm in ("voice", "music_vol"):
        assert 'slave.pcm "mono_out"' in _block(xvf, pcm), pcm
