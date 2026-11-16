"""server/install.sh (ledger 2026-11-09-friday-code-versioning, minor): a .py removed from the repo used to stay on the
VM forever (files were only copied over). Now .py files the repo no longer has are removed; anything else is kept."""

import os
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def _install(tmp: Path) -> subprocess.CompletedProcess:
    env = {**os.environ, "FRIDAY_OPT": str(tmp / "opt"), "FRIDAY_UNITS": str(tmp / "units"),
           "FRIDAY_NO_SYSTEMCTL": "1", "FRIDAY_VERSION_LINE": "v0.0.0 " + "a" * 40}
    return subprocess.run(["bash", str(REPO / "server" / "install.sh")], env=env, capture_output=True, text=True)


def test_a_py_removed_from_the_repo_is_removed_from_the_vm(tmp_path):
    status = tmp_path / "opt" / "status"
    status.mkdir(parents=True)
    (status / "gone_module.py").write_text("x = 1\n")
    (status / "notes.txt").write_text("kept\n")
    r = _install(tmp_path)
    assert r.returncode == 0, r.stderr
    assert not (status / "gone_module.py").exists()
    assert (status / "notes.txt").exists()  # only .py files are pruned
    assert any(p.suffix == ".py" for p in status.iterdir())  # the repo's own files are there


def test_the_cataloger_keeps_its_local_config(tmp_path):
    r = _install(tmp_path)
    assert r.returncode == 0, r.stderr
    assert (tmp_path / "opt" / "cataloger" / "local_config.py").exists()
