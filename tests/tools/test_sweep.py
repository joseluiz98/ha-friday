"""Sweep v2: private list + generic patterns + path-scoped allow rules + history mode (plan 2026-11-09 Task 3)."""
import importlib.util
import pathlib
import shutil
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("sweep", ROOT / "scripts" / "sweep.py")
sweep = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sweep)


def test_private_list_value_fails(tmp_path):
    (tmp_path / "a.py").write_text('OWNER = "Zelda"\n')
    assert sweep.sweep([str(tmp_path)], ["Zelda"], [])


def test_allow_rule_suppresses_translation_token_text(tmp_path):
    d = tmp_path / "translations"; d.mkdir()
    (d / "en.json").write_text('{"token": "Leave blank to keep the stored token."}\n')
    allow = sweep.parse_allow(['*/translations/*.json:"token"'])
    assert sweep.sweep([str(tmp_path)], [], allow) == []


def test_allow_rule_is_path_scoped(tmp_path):
    (tmp_path / "other.json").write_text('{"token": "Leave blank to keep the stored token."}\n')
    allow = sweep.parse_allow(['*/translations/*.json:"token"'])
    assert sweep.sweep([str(tmp_path)], [], allow)


def test_home_jose_allowed_other_home_fails(tmp_path):
    (tmp_path / "a.sh").write_text("cd /home/jose/satelite\n")
    assert sweep.sweep([str(tmp_path)], [], []) == []
    (tmp_path / "b.sh").write_text("cd /home/zelda/x\n")
    assert [f.path for f in sweep.sweep([str(tmp_path)], [], [])] == [str(tmp_path / "b.sh")]


def test_runs_without_private_list_using_generic_patterns(tmp_path):
    (tmp_path / "a.py").write_text('URL = "http://192.168.77.2:8701"\n')
    assert [f.category for f in sweep.sweep([str(tmp_path)], [], [])] == ["ip_mac"]


def test_loopback_is_not_house_data(tmp_path):
    (tmp_path / "a.py").write_text('HOST = "127.0.0.1"\n')
    assert sweep.sweep([str(tmp_path)], [], []) == []


@pytest.mark.skipif(shutil.which("git") is None, reason="git missing in the python:slim test image; runs in GitHub CI and on HAOS")
def test_history_mode_finds_value_only_in_old_commit(tmp_path):
    g = ["git", "-C", str(tmp_path), "-c", "user.email=t@t", "-c", "user.name=t"]
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "a.py").write_text('OWNER = "Zelda"\n')
    subprocess.run(g + ["add", "."], check=True); subprocess.run(g + ["commit", "-qm", "1"], check=True)
    (tmp_path / "a.py").write_text('OWNER = "x"\n')
    subprocess.run(g + ["commit", "-qam", "2"], check=True)
    assert sweep.sweep([str(tmp_path)], ["Zelda"], []) == []
    assert sweep.sweep_history(str(tmp_path), ["Zelda"], [])
