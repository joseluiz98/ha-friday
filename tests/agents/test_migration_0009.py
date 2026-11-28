"""Migration 0009 (27/11): installs the ALSA files with the `Fine` softvol. Activation runs migrations before it
installs system files, so 0009 installs them itself and then restarts what holds the audio output open."""
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MIG = ROOT / "satellite" / "migrations" / "0009_fine_volume.sh"
FILES = ("jabra.conf", "xvf3800.conf")


def run(root, rel=None):
    env = {"FRIDAY_OTA_ROOT": str(root), "PATH": "/usr/bin:/bin"}
    if rel is not None:
        env["FRIDAY_RELEASE_DIR"] = str(rel)
    return subprocess.run(["bash", str(MIG)], env=env, capture_output=True, text=True)


def release(tmp_path, names=FILES):
    rel = tmp_path / "release"
    (rel / "system").mkdir(parents=True)
    for n in names:
        (rel / "system" / n).write_text(n)
    return rel


def test_0009_installs_both_confs(tmp_path):
    root = tmp_path / "root"
    r = run(root, release(tmp_path))
    assert r.returncode == 0, r.stderr
    for n in FILES:
        dest = root / "etc/friday-audio" / n
        assert dest.read_text() == n and oct(dest.stat().st_mode & 0o777) == "0o644"


def test_0009_is_idempotent(tmp_path):
    root, rel = tmp_path / "root", release(tmp_path)
    assert run(root, rel).returncode == 0
    assert run(root, rel).returncode == 0


def test_0009_missing_source_touches_nothing(tmp_path):
    root = tmp_path / "root"
    r = run(root, release(tmp_path, ["jabra.conf"]))
    assert r.returncode != 0 and "xvf3800.conf" in r.stderr
    assert not (root / "etc/friday-audio").exists()


def test_0009_needs_the_release_dir(tmp_path):
    assert run(tmp_path / "root").returncode != 0


def test_0009_is_executable_and_never_restarts_under_a_test_root():
    assert os.access(MIG, os.X_OK)
    assert '[ -z "$R" ]' in MIG.read_text()
