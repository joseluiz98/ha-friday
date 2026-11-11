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


def test_escaped_mac_is_caught(tmp_path):
    (tmp_path / "a.py").write_text('return "02\\\\:00\\\\:5F\\\\:11\\\\:22\\\\:33\\n"\n')
    assert [f.category for f in sweep.sweep([str(tmp_path)], [], [])] == ["ip_mac"]


def test_allow_rule_never_hides_a_private_value(tmp_path):
    (tmp_path / "a.md").write_text("see github.com/joseluiz98 and Zelda\n")
    allow = sweep.parse_allow(["*:joseluiz98"])
    assert [f.category for f in sweep.sweep([str(tmp_path)], ["Zelda"], allow)] == ["person_name"]


def test_folder_named_after_a_resident_is_caught(tmp_path):
    (tmp_path / "zelda_notes").mkdir()
    (tmp_path / "zelda_notes" / "x.txt").write_text("nothing\n")
    found = sweep.sweep([str(tmp_path)], ["Zelda"], [])
    assert any(f.line is None and "zelda_notes" in f.path for f in found)


def test_common_api_key_shapes_are_secrets(tmp_path):
    (tmp_path / "k.env").write_text(
        "G=AIza" + "B" * 35 + "\n"
        "-----BEGIN PRIVATE KEY-----\n"
        "O=sk-" + "a" * 40 + "\n"
        "H=ghp_" + "c" * 36 + "\n"
        "P=github_pat_" + "d" * 60 + "\n"
        "E=sk_" + "e" * 48 + "\n")
    cats = [f.category for f in sweep.sweep([str(tmp_path)], [], [])]
    assert cats.count("secret") == 6


def test_private_value_inside_a_longer_word_is_not_a_hit(tmp_path):
    (tmp_path / "a.md").write_text("account joseluzelda98 and zeldas\n")
    assert sweep.sweep([str(tmp_path)], ["Zelda"], []) == []


def test_private_allow_rule_is_explicit(tmp_path):
    (tmp_path / "LICENSE").write_text("Copyright (c) 2026 Zelda\n")
    assert sweep.sweep([str(tmp_path)], ["Zelda"], sweep.parse_allow(["*/LICENSE:Copyright"]))
    assert sweep.sweep([str(tmp_path)], ["Zelda"], sweep.parse_allow(["private:*/LICENSE:^Copyright"])) == []


@pytest.mark.skipif(shutil.which("git") is None, reason="git missing in the python:slim test image; runs in GitHub CI and on HAOS")
def test_staged_mode_checks_the_index_not_the_working_tree(tmp_path):
    g = ["git", "-C", str(tmp_path), "-c", "user.email=t@t", "-c", "user.name=t"]
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "a.py").write_text('OWNER = "Zelda"\n')
    subprocess.run(g + ["add", "a.py"], check=True)
    (tmp_path / "a.py").write_text('OWNER = "x"\n')          # fixed only in the working tree
    assert sweep.sweep([str(tmp_path)], ["Zelda"], []) == []
    assert sweep.sweep_staged(str(tmp_path), ["Zelda"], [])


@pytest.mark.skipif(shutil.which("git") is None, reason="git missing in the python:slim test image; runs in GitHub CI and on HAOS")
def test_pre_push_scans_only_the_pushed_refs(tmp_path):
    """A finding on a branch that is not being pushed must not block the push of a clean branch (11/11)."""
    g = ["git", "-C", str(tmp_path), "-c", "user.email=t@t", "-c", "user.name=t"]
    subprocess.run(["git", "init", "-q", "-b", "main", str(tmp_path)], check=True)
    (tmp_path / "a.py").write_text('X = 1\n')
    subprocess.run(g + ["add", "."], check=True); subprocess.run(g + ["commit", "-qm", "1"], check=True)
    subprocess.run(g + ["checkout", "-qb", "other"], check=True)
    (tmp_path / "b.py").write_text('OWNER = "Zelda"\n')
    subprocess.run(g + ["add", "."], check=True); subprocess.run(g + ["commit", "-qm", "2"], check=True)
    rev = lambda r: subprocess.run(g + ["rev-parse", r], check=True, capture_output=True, text=True).stdout.strip()
    zero = "0" * 40
    main_line = f"refs/heads/main {rev('main')} refs/heads/main {zero}\n"
    other_line = f"refs/heads/other {rev('other')} refs/heads/other {zero}\n"
    assert sweep.pushed_revs(main_line) == [rev("main")]
    assert sweep.sweep_history(str(tmp_path), ["Zelda"], [], revs=sweep.pushed_revs(main_line)) == []
    assert sweep.sweep_history(str(tmp_path), ["Zelda"], [], revs=sweep.pushed_revs(other_line))
    # a deletion pushes nothing; the full history of a pushed ref is still scanned, not only the new commits
    assert sweep.pushed_revs(f"(delete) {zero} refs/heads/other {rev('other')}\n") == []
    assert sweep.sweep_history(str(tmp_path), ["Zelda"], []), "--history with no revs keeps scanning every ref"
