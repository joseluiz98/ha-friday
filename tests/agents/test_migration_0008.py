"""Migration 0008 (21/11): friday-netwatch.timer runs every minute through a systemd drop-in (OTA does not rewrite the
unit under /etc/systemd/system). Idempotent."""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MIG = ROOT / "satellite" / "migrations" / "0008_netwatch_every_minute.sh"
WANT = "[Timer]\nOnUnitActiveSec=\nOnUnitActiveSec=1min\n"


def run(root):
    return subprocess.run(["bash", str(MIG)],
                          env={"FRIDAY_OTA_ROOT": str(root), "PATH": f"{os.path.dirname(sys.executable)}:/usr/bin:/bin"},
                          capture_output=True, text=True)


def test_0008_writes_dropin(tmp_path):
    r = run(tmp_path)
    assert r.returncode == 0, r.stderr
    f = tmp_path / "etc/systemd/system/friday-netwatch.timer.d/interval.conf"
    assert f.read_text() == WANT


def test_0008_idempotent(tmp_path):
    assert run(tmp_path).returncode == 0
    assert run(tmp_path).returncode == 0
    d = tmp_path / "etc/systemd/system/friday-netwatch.timer.d"
    assert [p.name for p in d.iterdir()] == ["interval.conf"]
    assert (d / "interval.conf").read_text() == WANT
