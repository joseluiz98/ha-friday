"""guarded_restart.py program: an unreachable satellite program defers the restart (exit 0), it never fails the deploy."""
import importlib.util
import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("guarded_restart", ROOT / "scripts" / "lib" / "guarded_restart.py")
gr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gr)


def test_program_unreachable_is_deferred_not_failed(tmp_path, monkeypatch, capsys):
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"porta_local": 1, "senha_porta_local": "x"}))
    monkeypatch.setenv("FRIDAY_PROGRAM_CONFIG", str(cfg))
    monkeypatch.setenv("FRIDAY_PROGRAM_URL", "http://127.0.0.1:1")  # nothing listens on port 1
    monkeypatch.setenv("FRIDAY_PROGRAM_RESTART_CMD", "false")
    assert gr.program() == 0
    assert "restart deferred: program unreachable" in capsys.readouterr().out
