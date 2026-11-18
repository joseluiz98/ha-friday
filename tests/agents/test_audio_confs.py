"""ALSA configs per audio board (spec 2026-11-17 xvf3800-support, Task 1). The Jabra file is the old
satellite/asound.conf untouched: the MK1 must behave exactly as before."""
import hashlib
from pathlib import Path

AUDIO = Path(__file__).resolve().parents[2] / "satellite" / "audio"
# sha256 of satellite/asound.conf at origin/master (v0.3.8), before it moved to audio/jabra.conf
JABRA_SHA256 = "a7680856d2d4b2c3b7a0f4a4241c522871e0d34da609e516177be1e8e4cb3d38"
COMMON = ("pcm.voice", "pcm.music", "pcm.music_vol", "pcm.jabra_entrada", 'name "Music"')


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


def test_xvf_conf_controls_live_on_the_xvf_card():
    text = (AUDIO / "xvf3800.conf").read_text(encoding="utf-8")
    assert "card Array" in text and "card USB" not in text


def test_jabra_conf_unchanged():
    data = (AUDIO / "jabra.conf").read_bytes()
    assert hashlib.sha256(data).hexdigest() == JABRA_SHA256
