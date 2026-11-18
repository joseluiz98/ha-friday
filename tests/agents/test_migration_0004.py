"""Migration 0004 (17/11): the provisional jabra-only drop-ins of 15/11 leave; friday-audio-select decides now."""
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MIG = ROOT / "satellite" / "migrations" / "0004_audio_select.sh"
DROPS = ["etc/systemd/system/friday-satelite.service.d/jabra-only.conf",
         "etc/systemd/system/sendspin-cli.service.d/jabra-only.conf"]


def run(root):
    return subprocess.run(["bash", str(MIG)], env={"FRIDAY_OTA_ROOT": str(root), "PATH": "/usr/bin:/bin"},
                          capture_output=True, text=True)


def test_removes_the_jabra_only_dropins_and_keeps_others(tmp_path):
    for f in DROPS + ["etc/systemd/system/sendspin-cli.service.d/other.conf"]:
        (tmp_path / f).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / f).write_text("x")
    r = run(tmp_path)
    assert r.returncode == 0, r.stderr
    assert not any((tmp_path / f).exists() for f in DROPS)
    assert (tmp_path / "etc/systemd/system/sendspin-cli.service.d/other.conf").exists()


def test_idempotent(tmp_path):
    assert run(tmp_path).returncode == 0
    assert run(tmp_path).returncode == 0


SYSTEM_FILES = {"friday-audio-select": "usr/local/sbin/friday-audio-select",
                "jabra.conf": "etc/friday-satelite/asound/jabra.conf",
                "xvf3800.conf": "etc/friday-satelite/asound/xvf3800.conf",
                "90-friday-audio.rules": "etc/udev/rules.d/90-friday-audio.rules",
                "audio-board.conf": "etc/systemd/system/sendspin-cli.service.d/audio-board.conf",
                "friday-silence.service": "etc/systemd/system/friday-silence.service"}


def test_installs_the_audio_files_itself_for_an_older_ota_helper(tmp_path):
    # Review C1 (17/11): an update from v0.3.8 runs the OLD friday-ota-apply, whose SYSTEM map does not know these
    # files; without them the new friday-satelite.service ExecCondition points at a missing script and voice dies.
    rel = tmp_path / "release"
    (rel / "system").mkdir(parents=True)
    for n in SYSTEM_FILES:
        (rel / "system" / n).write_text(n)
    root = tmp_path / "root"
    r = subprocess.run(["bash", str(MIG)], env={"FRIDAY_OTA_ROOT": str(root), "FRIDAY_RELEASE_DIR": str(rel),
                                                "PATH": "/usr/bin:/bin"}, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    for n, dest in SYSTEM_FILES.items():
        assert (root / dest).read_text() == n, dest
    assert oct((root / SYSTEM_FILES["friday-audio-select"]).stat().st_mode & 0o777) == "0o755"


def test_missing_source_fails(tmp_path):
    rel = tmp_path / "release"
    (rel / "system").mkdir(parents=True)
    (rel / "system" / "jabra.conf").write_text("x")
    r = subprocess.run(["bash", str(MIG)], env={"FRIDAY_OTA_ROOT": str(tmp_path / "root"), "FRIDAY_RELEASE_DIR": str(rel),
                                                "PATH": "/usr/bin:/bin"}, capture_output=True, text=True)
    assert r.returncode == 1
    assert "missing" in r.stderr
