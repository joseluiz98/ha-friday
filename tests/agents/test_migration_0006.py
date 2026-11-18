"""Migration 0006 (18/11, owner): satellites with no extra wake word get "Sexta-feira" active, like the MK1 and like
new images. A satellite that already has extras is never touched, nor one without the model file."""
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MIG = ROOT / "satellite" / "migrations" / "0006_sexta_feira_default.sh"
SEXTA = {"nome": "sexta_feira", "arquivo": "/home/jose/modelos/sexta_feira.onnx", "limiar": 0.5, "modo": "ativa",
         "paciencia": 2}


def setup(tmp_path, cfg, model=True):
    etc = tmp_path / "etc/friday-satelite"
    etc.mkdir(parents=True)
    (etc / "config.json").write_text(json.dumps(cfg))
    os.chmod(etc / "config.json", 0o640)
    d = tmp_path / "home/jose/modelos"
    d.mkdir(parents=True)
    if model:
        (d / "sexta_feira.onnx").write_text("x")
    return etc / "config.json"


def run(root):
    return subprocess.run(["bash", str(MIG)],
                          env={"FRIDAY_OTA_ROOT": str(root), "PATH": f"{os.path.dirname(sys.executable)}:/usr/bin:/bin"},
                          capture_output=True, text=True)


def test_empty_extras_get_sexta_feira(tmp_path):
    cfg = setup(tmp_path, {"comodo": None, "palavras_extras": []})
    r = run(tmp_path)
    assert r.returncode == 0, r.stderr
    d = json.loads(cfg.read_text())
    assert d["palavras_extras"] == [SEXTA] and d["comodo"] is None
    assert oct(cfg.stat().st_mode & 0o777) == "0o640"
    assert run(tmp_path).returncode == 0 and json.loads(cfg.read_text())["palavras_extras"] == [SEXTA]  # idempotent


def test_missing_key_gets_sexta_feira(tmp_path):
    cfg = setup(tmp_path, {"volume": 4})
    assert run(tmp_path).returncode == 0
    assert json.loads(cfg.read_text())["palavras_extras"] == [SEXTA]


def test_existing_extras_are_kept(tmp_path):
    mine = [dict(SEXTA, limiar=0.4, modo="sombra", verificador="/home/jose/modelos/sexta_feira_verificador.pkl")]
    cfg = setup(tmp_path, {"palavras_extras": mine})
    assert run(tmp_path).returncode == 0
    assert json.loads(cfg.read_text())["palavras_extras"] == mine


def test_without_the_model_nothing_changes(tmp_path):
    cfg = setup(tmp_path, {"palavras_extras": []}, model=False)
    r = run(tmp_path)
    assert r.returncode == 0
    assert json.loads(cfg.read_text())["palavras_extras"] == []


def test_without_config_it_succeeds(tmp_path):
    assert run(tmp_path).returncode == 0
