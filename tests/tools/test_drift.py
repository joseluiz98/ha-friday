"""Drift check (plan 2026-11-09 Task 9): what a host runs against what the repo says it should run."""
import hashlib
import importlib.util
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("drift", ROOT / "scripts" / "drift.py")
drift = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(drift)


def _h(b):
    return hashlib.sha256(b).hexdigest()


def _tree(root, files):
    for rel, data in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)


def test_identical_tree_no_diff(tmp_path):
    _tree(tmp_path, {"a.py": b"1", "sub/b.py": b"2"})
    assert drift.drift({"a.py": _h(b"1"), "sub/b.py": _h(b"2")}, str(tmp_path)) == []


def test_reports_changed_missing_extra(tmp_path):
    _tree(tmp_path, {"a.py": b"edited", "extra.py": b"x"})
    out = sorted(drift.drift({"a.py": _h(b"1"), "gone.py": _h(b"2")}, str(tmp_path)))
    assert out == [("a.py", "changed"), ("extra.py", "extra"), ("gone.py", "missing")]


def test_ignores_pycache_and_version_file(tmp_path):
    _tree(tmp_path, {"a.py": b"1", "__pycache__/a.cpython-313.pyc": b"c", "FRIDAY_VERSION": b"v",
                     "venv/bin/python": b"p", "x.py.bak.20261101": b"old"})
    assert drift.drift({"a.py": _h(b"1")}, str(tmp_path)) == []


def test_expected_tree_from_git_paths_applies_filters():
    files = {"satellite/program/maestro.py": b"m", "satellite/program/test_maestro.py": b"t",
             "satellite/program/install.sh": b"i"}
    exp = drift.expected(files, "satellite/program/", skip_tests=True, skip=("install.sh",))
    assert exp == {"maestro.py": _h(b"m")}
