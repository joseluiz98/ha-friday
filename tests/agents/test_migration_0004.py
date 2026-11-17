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
