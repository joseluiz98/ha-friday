"""Migration 0008 (23/11): the ALSA board files move to /etc/friday-audio (0755) so the music player, which cannot enter the
0700 /etc/friday-satelite, can read what /etc/asound.conf points to."""
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MIG = ROOT / "satellite" / "migrations" / "0008_audio_readable.sh"
FILES = ("jabra.conf", "xvf3800.conf", "friday-audio-select")
OLD = "etc/friday-satelite"


def run(root, rel):
    env = {"FRIDAY_OTA_ROOT": str(root), "FRIDAY_RELEASE_DIR": str(rel), "PATH": "/usr/bin:/bin"}
    return subprocess.run(["bash", str(MIG)], env=env, capture_output=True, text=True)


def release(tmp_path, names=FILES):
    rel = tmp_path / "release"
    (rel / "system").mkdir(parents=True)
    for n in names:
        (rel / "system" / n).write_text(n)
    return rel


def board(root, name):
    (root / OLD).mkdir(parents=True, exist_ok=True)
    (root / OLD / "audio_board").write_text(name + "\n")


def link(root):
    return os.readlink(root / "etc/asound.conf")


def test_0008_installs_readable_dir(tmp_path):
    root = tmp_path / "root"
    r = run(root, release(tmp_path))
    assert r.returncode == 0, r.stderr
    assert oct((root / "etc/friday-audio").stat().st_mode & 0o777) == "0o755"
    for n in ("jabra.conf", "xvf3800.conf"):
        assert (root / "etc/friday-audio" / n).read_text() == n
        assert oct((root / "etc/friday-audio" / n).stat().st_mode & 0o777) == "0o644"
    sel = root / "usr/local/sbin/friday-audio-select"
    assert sel.read_text() == "friday-audio-select" and oct(sel.stat().st_mode & 0o777) == "0o755"


def test_0008_repoints_link_jabra(tmp_path):
    root = tmp_path / "root"
    rel = release(tmp_path)
    assert run(root, rel).returncode == 0  # no audio_board file at all: jabra
    assert link(root) == "/etc/friday-audio/jabra.conf"
    root2 = tmp_path / "root2"
    board(root2, "none")
    assert run(root2, rel).returncode == 0
    assert link(root2) == "/etc/friday-audio/jabra.conf"


def test_0008_repoints_old_link(tmp_path):
    root = tmp_path / "root"
    (root / "etc").mkdir(parents=True)
    os.symlink("/etc/friday-satelite/asound/jabra.conf", root / "etc/asound.conf")
    assert run(root, release(tmp_path)).returncode == 0
    assert link(root) == "/etc/friday-audio/jabra.conf"


def test_0008_keeps_board(tmp_path):
    root = tmp_path / "root"
    board(root, "xvf3800")
    assert run(root, release(tmp_path)).returncode == 0
    assert link(root) == "/etc/friday-audio/xvf3800.conf"
    assert (root / OLD / "audio_board").read_text().strip() == "xvf3800"


def test_0008_keeps_old_files(tmp_path):
    root = tmp_path / "root"
    (root / OLD / "asound").mkdir(parents=True)
    (root / OLD / "asound/jabra.conf").write_text("old")
    assert run(root, release(tmp_path)).returncode == 0
    assert (root / OLD / "asound/jabra.conf").read_text() == "old"  # a rollback to v0.4.3 still finds its target


def test_0008_does_not_chmod_friday_satelite(tmp_path):
    root = tmp_path / "root"
    (root / OLD).mkdir(parents=True)
    os.chmod(root / OLD, 0o700)
    assert run(root, release(tmp_path)).returncode == 0
    assert oct((root / OLD).stat().st_mode & 0o777) == "0o700"


def test_0008_idempotent(tmp_path):
    root = tmp_path / "root"
    rel = release(tmp_path)
    board(root, "xvf3800")
    assert run(root, rel).returncode == 0
    assert run(root, rel).returncode == 0
    assert link(root) == "/etc/friday-audio/xvf3800.conf"
    assert (root / "etc/friday-audio/jabra.conf").read_text() == "jabra.conf"


def test_0008_missing_source_fails(tmp_path):
    r = run(tmp_path / "root", release(tmp_path, ["xvf3800.conf", "friday-audio-select"]))
    assert r.returncode != 0
    assert "jabra.conf" in r.stderr
