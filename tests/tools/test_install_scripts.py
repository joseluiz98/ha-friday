"""Install scripts (plan 2026-11-09 Task 8): files swapped, FRIDAY_VERSION written, restarts only through the guards."""
import http.server
import json
import os
import pathlib
import subprocess
import threading

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
LINE = "v0.2.0 0123456789abcdef0123456789abcdef01234567"


def _server(code: int, body: dict):
    class H(http.server.BaseHTTPRequestHandler):
        def _send(self):
            data = json.dumps(body).encode()
            self.send_response(code); self.send_header("Content-Length", str(len(data))); self.end_headers()
            self.wfile.write(data)
        do_GET = do_POST = _send

        def log_message(self, *a):
            pass
    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


def _run(script, env):
    return subprocess.run(["bash", str(ROOT / script)], env={**os.environ, **env}, capture_output=True, text=True)


def test_install_defers_restart_when_conversation_open(tmp_path):
    srv, url = _server(409, {"error": "conversation_open"})
    tok = tmp_path / "tok"; tok.write_text("t")
    app = tmp_path / "app"
    r = _run("voice_gateway/install.sh", {"FRIDAY_APP_DIR": str(app), "FRIDAY_VERSION_LINE": LINE,
                                          "FRIDAY_RESTART_URL": url + "/v1/restart", "FRIDAY_STATUS_TOKEN_FILE": str(tok)})
    srv.shutdown()
    assert r.returncode == 0, r.stderr
    assert "restart deferred: conversation open" in r.stdout
    assert (app / "bot.py").exists() and (app / "FRIDAY_VERSION").read_text().strip() == LINE
    assert not list(app.glob(".*.new"))


def test_install_requests_restart_when_idle(tmp_path):
    srv, url = _server(202, {"restarting": True})
    tok = tmp_path / "tok"; tok.write_text("t")
    r = _run("voice_gateway/install.sh", {"FRIDAY_APP_DIR": str(tmp_path / "app"), "FRIDAY_VERSION_LINE": LINE,
                                          "FRIDAY_RESTART_URL": url + "/v1/restart", "FRIDAY_STATUS_TOKEN_FILE": str(tok)})
    srv.shutdown()
    assert r.returncode == 0 and "restart requested" in r.stdout


def test_program_install_defers_when_satellite_busy(tmp_path):
    srv, url = _server(200, {"estado": "ouvindo", "timers": [], "alarme": None})
    cfg = tmp_path / "config.json"; cfg.write_text(json.dumps({"senha_porta_local": "x"}))
    marker = tmp_path / "restarted"
    r = _run("satellite/program/install.sh", {"FRIDAY_PROGRAM_DIR": str(tmp_path / "satelite"), "FRIDAY_VERSION_LINE": LINE,
                                              "FRIDAY_PROGRAM_CONFIG": str(cfg), "FRIDAY_PROGRAM_URL": url,
                                              "FRIDAY_PROGRAM_RESTART_CMD": f"touch {marker}"})
    srv.shutdown()
    assert r.returncode == 0, r.stderr
    assert "restart deferred: satellite busy" in r.stdout and not marker.exists()
    prog = tmp_path / "satelite"
    assert (prog / "maestro.py").exists() and not (prog / "test_maestro.py").exists()
    # deferred restart: the running code is still the old one, so only the pending marker carries the new line
    assert (prog / "FRIDAY_VERSION.pending").read_text().strip() == LINE and not (prog / "FRIDAY_VERSION").exists()


def test_program_install_restarts_when_idle(tmp_path):
    srv, url = _server(200, {"estado": "parada", "timers": [], "alarme": None})
    cfg = tmp_path / "config.json"; cfg.write_text(json.dumps({"senha_porta_local": "x"}))
    marker = tmp_path / "restarted"
    r = _run("satellite/program/install.sh", {"FRIDAY_PROGRAM_DIR": str(tmp_path / "satelite"), "FRIDAY_VERSION_LINE": LINE,
                                              "FRIDAY_PROGRAM_CONFIG": str(cfg), "FRIDAY_PROGRAM_URL": url,
                                              "FRIDAY_PROGRAM_RESTART_CMD": f"touch {marker}"})
    srv.shutdown()
    assert r.returncode == 0 and marker.exists()


def test_skills_install_leaves_brain_made_skills(tmp_path):
    ws = tmp_path / "ws"
    (ws / "skills" / "brain-made").mkdir(parents=True)
    (ws / "skills" / "brain-made" / "SKILL.md").write_text("mine")
    env_file = tmp_path / "local.env"
    env_file.write_text("FRIDAY_OWNER_NAME=Alex\nFRIDAY_OWNER_NOTIFY=notify/x\nFRIDAY_SAT_OFFICE=sat-0a1b2c\n")
    r = _run("brain/skills/install.sh", {"FRIDAY_WORKSPACE": str(ws), "FRIDAY_LOCAL_CONFIG": str(env_file),
                                        "FRIDAY_VERSION_LINE": LINE})
    assert r.returncode == 0, r.stderr
    assert (ws / "skills" / "brain-made" / "SKILL.md").read_text() == "mine"
    text = (ws / "skills" / "falar-em-casa" / "SKILL.md").read_text()
    assert "{DONO}" not in text and "Alex" in text and "sat-0a1b2c" in text
    assert (ws / "bin" / "voice_skills.sh").exists()


def test_skills_install_refuses_missing_key(tmp_path):
    env_file = tmp_path / "local.env"; env_file.write_text("FRIDAY_OWNER_NAME=Alex\n")
    r = _run("brain/skills/install.sh", {"FRIDAY_WORKSPACE": str(tmp_path / "ws"), "FRIDAY_LOCAL_CONFIG": str(env_file),
                                        "FRIDAY_VERSION_LINE": LINE})
    assert r.returncode != 0 and "FRIDAY_OWNER_NOTIFY" in r.stderr
    assert not (tmp_path / "ws" / "skills" / "falar-em-casa").exists()


def test_wwt_install_puts_training_code_and_nightly_script(tmp_path):
    """The VM nightly featcache runs the repo's training code from /opt/friday/wwt/training, not the HAOS mount."""
    dest = tmp_path / "wwt"
    r = _run("tools/wakeword/wwt/install.sh", {"FRIDAY_WWT_DIR": str(dest), "FRIDAY_VERSION_LINE": LINE})
    assert r.returncode == 0, r.stderr
    assert (dest / "featcache_nightly.sh").exists() and os.access(dest / "featcache_nightly.sh", os.X_OK)
    assert (dest / "training" / "featcache.py").exists() and not (dest / "training" / "test_gate.py").exists()
    assert (dest / "training" / "FRIDAY_VERSION").read_text().strip() == LINE
    nightly = (dest / "featcache_nightly.sh").read_text()
    assert "/mnt/haos" not in nightly and "/opt/friday/wwt/training" in nightly
    assert not list(dest.glob(".*.new")) and not (dest / ".training.new").exists()
