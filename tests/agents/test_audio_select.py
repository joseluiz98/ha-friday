"""friday-audio-select (phase B, spec 2026-11-17-xvf3800-support): picks the audio board at every program/player start
(ExecCondition) and on hotplug; the XVF3800 wins over the Jabra; no board or voice intent off = the unit is skipped."""
import importlib.machinery
import importlib.util
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "satellite" / "audio" / "friday-audio-select"


def load():
    loader = importlib.machinery.SourceFileLoader("friday_audio_select", str(SCRIPT))
    spec = importlib.util.spec_from_loader("friday_audio_select", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def usb(tmp_path, *ids):
    d = tmp_path / "sys"
    for i, (vid, pid) in enumerate(ids):
        dev = d / f"1-1.{i}"
        dev.mkdir(parents=True)
        (dev / "idVendor").write_text(vid + "\n")
        (dev / "idProduct").write_text(pid + "\n")
    d.mkdir(parents=True, exist_ok=True)
    return str(d)


def root(tmp_path, intent=None):
    r = tmp_path / "root"
    (r / "etc/friday-satelite/asound").mkdir(parents=True)
    for b in ("jabra", "xvf3800"):
        (r / f"etc/friday-satelite/asound/{b}.conf").write_text(b)
    if intent is not None:
        (r / "home/jose/.friday-agent").mkdir(parents=True)
        (r / "home/jose/.friday-agent/voice-enabled").write_text(intent + "\n")
    return str(r)


class Run:
    def __init__(self):
        self.calls = []

    def __call__(self, argv, **kw):
        self.calls.append(argv)


def test_detect_prefers_xvf_over_jabra(tmp_path):
    m = load()
    assert m.detect(usb(tmp_path, ("0b0e", "0410"), ("2886", "001a"))) == "xvf3800"


def test_detect_jabra_and_none(tmp_path):
    m = load()
    assert m.detect(usb(tmp_path / "a", ("0b0e", "0410"))) == "jabra"
    assert m.detect(usb(tmp_path / "b", ("1234", "5678"))) == "none"
    assert m.detect(str(tmp_path / "missing")) == "none"


def test_apply_points_symlink_and_writes_board_and_mic(tmp_path):
    m, r, run = load(), root(tmp_path), Run()
    assert m.apply("xvf3800", r, run) is True
    assert os.readlink(os.path.join(r, "etc/asound.conf")) == "/etc/friday-satelite/asound/xvf3800.conf"
    assert open(os.path.join(r, "etc/friday-satelite/audio_board")).read().strip() == "xvf3800"
    assert open(os.path.join(r, "etc/friday-satelite/mic")).read().strip() == "xvf3800"


def test_old_asound_file_is_kept_aside_once(tmp_path):
    m, r = load(), root(tmp_path)
    open(os.path.join(r, "etc/asound.conf"), "w").write("old")
    m.apply("jabra", r, Run())
    assert open(os.path.join(r, "etc/asound.conf.bak.audio-select")).read() == "old"
    assert os.path.islink(os.path.join(r, "etc/asound.conf"))


def test_none_keeps_jabra_conf_and_reports_none(tmp_path):
    m, r = load(), root(tmp_path)
    m.apply("none", r, Run())
    assert os.readlink(os.path.join(r, "etc/asound.conf")) == "/etc/friday-satelite/asound/jabra.conf"
    assert open(os.path.join(r, "etc/friday-satelite/audio_board")).read().strip() == "none"


def test_same_board_does_nothing(tmp_path):
    m, r, run = load(), root(tmp_path), Run()
    m.apply("jabra", r, run)
    assert m.apply("jabra", r, run) is False


def test_check_exit_codes(tmp_path):
    m = load()
    sys_x = usb(tmp_path / "x", ("2886", "001a"))
    assert m.check(sys_x, root(tmp_path / "on", "on"), Run()) == 0
    assert m.check(usb(tmp_path / "n"), root(tmp_path / "none", "on"), Run()) == 1


def test_intent_off_keeps_services_down(tmp_path):
    m, run = load(), Run()
    r = root(tmp_path, "off")
    assert m.check(usb(tmp_path / "x", ("2886", "001a")), r, run) == 1
    # the board files are still kept current, so turning the voice on later starts on the right board
    assert open(os.path.join(r, "etc/friday-satelite/audio_board")).read().strip() == "xvf3800"


def test_silence_follows_the_board(tmp_path):
    m = load()
    run = Run()
    m.check(usb(tmp_path / "x", ("2886", "001a")), root(tmp_path / "a", "on"), run)
    assert ["systemctl", "--no-block", "start", "friday-silence.service"] in run.calls
    run2 = Run()
    m.check(usb(tmp_path / "n"), root(tmp_path / "b", "on"), run2)
    assert ["systemctl", "--no-block", "stop", "friday-silence.service"] in run2.calls


def test_units_use_the_check():
    sat = (ROOT / "satellite/program/friday-satelite.service").read_text()
    assert "ExecCondition=+/usr/local/sbin/friday-audio-select --check" in sat
    drop = (ROOT / "satellite/audio/audio-board.conf").read_text()
    assert "ExecCondition=+/usr/local/sbin/friday-audio-select --check" in drop
    sil = (ROOT / "satellite/audio/friday-silence.service").read_text()
    assert "/dev/zero" in sil and "-D voice" in sil and "User=jose" in sil
    rules = (ROOT / "satellite/audio/90-friday-audio.rules").read_text()
    assert "2886/1a/" in rules and "b0e/410/" in rules and "friday-satelite.service" in rules
