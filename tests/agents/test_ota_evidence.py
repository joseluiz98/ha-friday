"""Failure logs kept off the VM before a snapshot restore (FINDING 9)."""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "agents" / "server"))
from friday_server_agent.ota import evidence as ev  # noqa: E402


def test_writes_one_folder_per_failure_and_keeps_the_newest(tmp_path):
    root = tmp_path / "logs" / "friday" / "ota"
    t = [1_800_000_000.0]
    for i in range(12):
        t[0] += 60
        d = ev.save("v0.3.3", {"run.json": "{}", "docker.log": f"try {i}"}, root=str(root), clock=lambda: t[0], keep=10)
    dirs = sorted(os.listdir(root))
    assert len(dirs) == 10 and all(d.startswith("v0.3.3-") for d in dirs)
    assert (root / dirs[-1] / "docker.log").read_text() == "try 11"


def test_an_unavailable_share_never_raises(tmp_path):
    f = tmp_path / "file"
    f.write_text("x")
    assert ev.save("v0.3.3", {"a": "b"}, root=str(f / "sub")) is None
