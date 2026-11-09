"""Installed version (plan 2026-11-09 Task 8): the agents publish what FRIDAY_VERSION says, never a constant."""
import importlib
import sys
from pathlib import Path

for _sub in ("satellite", "server"):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "agents" / _sub))

from friday_agent_core import version


def test_sw_version_from_version_file(tmp_path):
    f = tmp_path / "FRIDAY_VERSION"
    f.write_text("v0.2.0 0123456789abcdef0123456789abcdef01234567\n")
    assert version.installed_version(str(f)) == "v0.2.0"
    assert version.installed_sha(str(f)) == "0123456789abcdef0123456789abcdef01234567"


def test_sw_version_unknown_without_file(tmp_path):
    assert version.installed_version(str(tmp_path / "absent")) == "unknown"
    assert version.installed_sha(str(tmp_path / "absent")) is None


def test_version_file_lives_next_to_the_core_package():
    assert version.FILE.endswith("friday_agent_core/FRIDAY_VERSION")


def test_agents_use_the_installed_version(monkeypatch, tmp_path):
    f = tmp_path / "FRIDAY_VERSION"
    f.write_text("v9.8.7 abc\n")
    monkeypatch.setattr(version, "FILE", str(f))
    from friday_satellite_agent import capabilities
    from friday_server_agent import collectors
    assert importlib.reload(capabilities).VERSION == "v9.8.7"
    assert importlib.reload(collectors).VERSION == "v9.8.7"
