"""Imported code carries no house data (plan 2026-11-09 Tasks 6-7). Uses the private list when present."""
import importlib.util
import os
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("sweep", ROOT / "scripts" / "sweep.py")
sweep = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sweep)


def _private():
    lst = os.environ.get("FRIDAY_SENSITIVE_LIST", sweep.DEFAULT_LIST)
    return sweep.scan.load_known(lst) if os.path.exists(lst) else []


def _allow():
    return sweep.parse_allow(open(ROOT / ".sweep-allow", encoding="utf-8"))


@pytest.mark.parametrize("folder", ["voice_gateway", "server"])
def test_folder_has_no_literal_house_value(folder):
    found = sweep.sweep([str(ROOT / folder)], _private(), _allow())
    assert found == [], [f"{f.path}:{f.line} {f.category}" for f in found][:20]
