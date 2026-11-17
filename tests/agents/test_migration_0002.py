"""Migration 0002: the old status page (satelite-status) leaves the satellite; the agent reports the health."""
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MIG = ROOT / "satellite" / "migrations" / "0002_retire_status_page.sh"
FILES = ["etc/systemd/system/satelite-status.timer", "etc/systemd/system/satelite-status.service",
         "etc/systemd/system/timers.target.wants/satelite-status.timer", "usr/local/bin/satelite-status.sh"]


def run(root):
    return subprocess.run(["bash", str(MIG)], env={"FRIDAY_OTA_ROOT": str(root), "PATH": "/usr/bin:/bin"},
                          capture_output=True, text=True)


def test_removes_the_status_page_and_keeps_the_satellite_identity(tmp_path):
    for f in FILES + ["etc/satelite/id", "etc/satelite/webhook_id"]:
        (tmp_path / f).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / f).write_text("x")
    r = run(tmp_path)
    assert r.returncode == 0, r.stderr
    assert not any((tmp_path / f).exists() for f in FILES)
    assert (tmp_path / "etc/satelite/id").exists() and (tmp_path / "etc/satelite/webhook_id").exists()


def test_idempotent_on_a_satellite_without_it(tmp_path):
    assert run(tmp_path).returncode == 0
    assert run(tmp_path).returncode == 0


def test_nothing_ships_the_status_page_any_more():
    assert not (ROOT / "satellite" / "status").exists()
    for rel in ("ota/release.json", "agents/server/friday_server_agent/ota/packages.py",
                "agents/satellite/friday-ota-apply", "satellite/image/stage.sh", "satellite/image/customize-chroot.sh"):
        assert "satelite-status" not in (ROOT / rel).read_text() and "satellite/status" not in (ROOT / rel).read_text(), rel
