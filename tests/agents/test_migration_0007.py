"""Migration 0007 (18/11, owner): satellites record activations and ambient by default. It only acts when neither was
adopted (both keys off or absent); any partial adoption is left byte for byte. Keeps the file's mode and owner."""
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MIG = ROOT / "satellite" / "migrations" / "0007_recordings_default.sh"


def setup(tmp_path, cfg):
    etc = tmp_path / "etc/friday-satelite"
    etc.mkdir(parents=True)
    (etc / "config.json").write_text(json.dumps(cfg))
    os.chmod(etc / "config.json", 0o640)
    return etc / "config.json"


def run(root):
    return subprocess.run(["bash", str(MIG)],
                          env={"FRIDAY_OTA_ROOT": str(root), "PATH": f"{os.path.dirname(sys.executable)}:/usr/bin:/bin"},
                          capture_output=True, text=True)


def test_0007_turns_both_on_when_nothing_adopted(tmp_path):
    cfg = setup(tmp_path, {"comodo": "sala", "gravar_ativacoes": False, "gravar_ambiente_janela": None})
    r = run(tmp_path)
    assert r.returncode == 0, r.stderr
    d = json.loads(cfg.read_text())
    assert d["gravar_ativacoes"] is True and d["gravar_ambiente_janela"] == "08:00-22:00"
    assert d["comodo"] == "sala"
    assert oct(cfg.stat().st_mode & 0o777) == "0o640"


def test_0007_keys_absent_counts_as_not_adopted(tmp_path):
    cfg = setup(tmp_path, {})
    assert run(tmp_path).returncode == 0
    d = json.loads(cfg.read_text())
    assert d["gravar_ativacoes"] is True and d["gravar_ambiente_janela"] == "08:00-22:00"


def test_0007_leaves_partial_adoption_alone(tmp_path):
    for i, cfg_in in enumerate(({"gravar_ativacoes": True, "gravar_ambiente_janela": None},
                                {"gravar_ativacoes": False, "gravar_ambiente_janela": "09:00-18:00"})):
        root = tmp_path / str(i)
        cfg = setup(root, cfg_in)
        before = cfg.read_bytes()
        r = run(root)
        assert r.returncode == 0, r.stderr
        assert cfg.read_bytes() == before


def test_0007_idempotent(tmp_path):
    cfg = setup(tmp_path, {"gravar_ativacoes": False, "gravar_ambiente_janela": None})
    assert run(tmp_path).returncode == 0
    once = cfg.read_bytes()
    assert run(tmp_path).returncode == 0
    assert cfg.read_bytes() == once


def test_0007_no_config_is_ok(tmp_path):
    r = run(tmp_path)
    assert r.returncode == 0
    assert "nothing to do" in r.stdout
