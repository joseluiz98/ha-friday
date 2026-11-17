"""Migration 0003 (17/11): satellites still on the old factory tones move to the ElevenLabs set (the owner's choice
since 20/10). A tone someone picked by hand is never touched, nor one whose new file is missing."""
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MIG = ROOT / "satellite" / "migrations" / "0003_elevenlabs_tones.sh"
NEW = {"inicio": "inicio_elevenlabs.wav", "fim": "fim_elevenlabs_20261020.wav", "erro": "erro_b.wav",
       "alarme": "alarme_elevenlabs.wav", "lembrete": "lembrete_elevenlabs.wav"}


def setup(tmp_path, sons, files=tuple(NEW.values())):
    etc = tmp_path / "etc/friday-satelite"
    etc.mkdir(parents=True)
    (etc / "config.json").write_text(json.dumps({"comodo": "Sala", "sons": sons}))
    os.chmod(etc / "config.json", 0o600)
    d = tmp_path / "home/jose/satelite/sons"
    d.mkdir(parents=True)
    for f in files:
        (d / f).write_text("x")
    return etc / "config.json"


def run(root):
    return subprocess.run(["bash", str(MIG)], env={"FRIDAY_OTA_ROOT": str(root), "PATH": f"{os.path.dirname(sys.executable)}:/usr/bin:/bin"},
                          capture_output=True, text=True)


def test_old_factory_tones_become_elevenlabs(tmp_path):
    cfg = setup(tmp_path, {"inicio": "inicio_a.wav", "fim": "fim_a.wav", "erro": "erro_a.wav",
                           "volume": "volume_a.wav", "alarme": "alarme_a.wav", "lembrete": "lembrete_a.wav"})
    r = run(tmp_path)
    assert r.returncode == 0, r.stderr
    d = json.loads(cfg.read_text())
    assert d["sons"] == {**NEW, "volume": "volume_a.wav"}
    assert d["comodo"] == "Sala"
    assert oct(cfg.stat().st_mode & 0o777) == "0o600"


def test_hand_picked_tones_are_kept(tmp_path):
    cfg = setup(tmp_path, {"inicio": "inicio_c.wav", "fim": "fim_a.wav"})
    assert run(tmp_path).returncode == 0
    d = json.loads(cfg.read_text())
    assert d["sons"]["inicio"] == "inicio_c.wav" and d["sons"]["fim"] == NEW["fim"]


def test_missing_new_file_keeps_the_old_tone(tmp_path):
    cfg = setup(tmp_path, {"alarme": "alarme_a.wav"}, files=())
    assert run(tmp_path).returncode == 0
    assert json.loads(cfg.read_text())["sons"]["alarme"] == "alarme_a.wav"


def test_idempotent_and_safe_without_config(tmp_path):
    assert run(tmp_path).returncode == 0
    cfg = setup(tmp_path / "x", {"inicio": "inicio_a.wav"})
    assert run(tmp_path / "x").returncode == 0
    first = cfg.read_text()
    assert run(tmp_path / "x").returncode == 0
    assert cfg.read_text() == first
