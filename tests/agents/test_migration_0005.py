"""Migration 0005 (18/11): reinstalls the audio files 0004 left out; fails when the release lacks them."""
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MIG = ROOT / "satellite" / "migrations" / "0005_audio_files.sh"
SYSTEM_FILES = {"friday-audio-select": "usr/local/sbin/friday-audio-select",
                "jabra.conf": "etc/friday-satelite/asound/jabra.conf",
                "xvf3800.conf": "etc/friday-satelite/asound/xvf3800.conf",
                "90-friday-audio.rules": "etc/udev/rules.d/90-friday-audio.rules",
                "audio-board.conf": "etc/systemd/system/sendspin-cli.service.d/audio-board.conf",
                "friday-silence.service": "etc/systemd/system/friday-silence.service"}


def run(root, rel=None):
    env = {"FRIDAY_OTA_ROOT": str(root), "PATH": "/usr/bin:/bin"}
    if rel is not None:
        env["FRIDAY_RELEASE_DIR"] = str(rel)
    return subprocess.run(["bash", str(MIG)], env=env, capture_output=True, text=True)


def release(tmp_path, names=SYSTEM_FILES):
    rel = tmp_path / "release"
    (rel / "system").mkdir(parents=True)
    for n in names:
        (rel / "system" / n).write_text(n)
    return rel


def test_installs_all_audio_files_with_modes(tmp_path):
    root = tmp_path / "root"
    r = run(root, release(tmp_path))
    assert r.returncode == 0, r.stderr
    for n, dest in SYSTEM_FILES.items():
        assert (root / dest).read_text() == n, dest
    assert oct((root / SYSTEM_FILES["friday-audio-select"]).stat().st_mode & 0o777) == "0o755"
    assert oct((root / SYSTEM_FILES["jabra.conf"]).stat().st_mode & 0o777) == "0o644"


def test_idempotent(tmp_path):
    root = tmp_path / "root"
    rel = release(tmp_path)
    assert run(root, rel).returncode == 0
    assert run(root, rel).returncode == 0
    for n, dest in SYSTEM_FILES.items():
        assert (root / dest).read_text() == n


def test_missing_source_fails(tmp_path):
    r = run(tmp_path / "root", release(tmp_path, ["jabra.conf"]))
    assert r.returncode == 1
    assert "0005: missing friday-audio-select in the release system files" in r.stderr


def test_without_release_dir_fails(tmp_path):
    r = run(tmp_path / "root")
    assert r.returncode == 1
    assert "FRIDAY_RELEASE_DIR not set" in r.stderr
