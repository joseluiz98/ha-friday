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


def _wwt_env(tmp_path, office="sat-0a1b2c"):
    for d in ("cache", "incoming", "systemd"):
        (tmp_path / d).mkdir(exist_ok=True)
    env_file = tmp_path / "local.env"
    env_file.write_text(f"FRIDAY_TELEGRAM_CHAT=1\nFRIDAY_SAT_OFFICE={office}\n" if office else "FRIDAY_TELEGRAM_CHAT=1\n")
    return {"FRIDAY_WWT_DIR": str(tmp_path / "wwt"), "FRIDAY_VERSION_LINE": LINE,
            "FRIDAY_WWT_CACHE": str(tmp_path / "cache"), "FRIDAY_WAKEWORD_INCOMING": str(tmp_path / "incoming"),
            "FRIDAY_SYSTEMD_DIR": str(tmp_path / "systemd"), "FRIDAY_LOCAL_CONFIG": str(env_file)}


def test_wwt_install_puts_training_code_and_nightly_script(tmp_path):
    """The VM nightly featcache runs the repo's training code from /opt/friday/wwt/training, not the HAOS mount."""
    dest = tmp_path / "wwt"
    r = _run("tools/wakeword/wwt/install.sh", _wwt_env(tmp_path))
    assert r.returncode == 0, r.stderr
    assert (dest / "featcache_nightly.sh").exists() and os.access(dest / "featcache_nightly.sh", os.X_OK)
    assert (dest / "training" / "featcache.py").exists() and not (dest / "training" / "test_gate.py").exists()
    assert (dest / "training" / "FRIDAY_VERSION").read_text().strip() == LINE
    nightly = (dest / "featcache_nightly.sh").read_text()
    assert "/mnt/haos" not in nightly and "/opt/friday/wwt/training" in nightly
    assert not list(dest.glob(".*.new")) and not (dest / ".training.new").exists()


def test_wwt_install_renames_legacy_office_cache_once(tmp_path):
    """Legacy caches amb<YYYYMMDD>.* are the office satellite's: renamed to amb<YYYYMMDD>_<sat>.*, idempotently."""
    env = _wwt_env(tmp_path)
    c = tmp_path / "cache"
    for ext in ("emb.npy", "json", "log", "vad.npy"):
        (c / f"amb20261103.{ext}").write_text(ext)
    (c / "amb20261104_sat-1d2e3f.json").write_text("other")
    (c / "score_v1.json").write_text("{}"); (c / "sessoes.json").write_text("{}")
    for _ in range(2):
        r = _run("tools/wakeword/wwt/install.sh", env)
        assert r.returncode == 0, r.stderr
        assert sorted(p.name for p in c.iterdir()) == sorted(
            [f"amb20261103_sat-0a1b2c.{e}" for e in ("emb.npy", "json", "log", "vad.npy")]
            + ["amb20261104_sat-1d2e3f.json", "score_v1.json", "sessoes.json"])
    assert (c / "amb20261103_sat-0a1b2c.json").read_text() == "json"


def test_wwt_install_refuses_legacy_cache_without_office(tmp_path):
    env = _wwt_env(tmp_path, office=None)
    (tmp_path / "cache" / "amb20261103.json").write_text("{}")
    r = _run("tools/wakeword/wwt/install.sh", env)
    assert r.returncode != 0 and "FRIDAY_SAT_OFFICE" in r.stderr
    assert (tmp_path / "cache" / "amb20261103.json").exists() and not (tmp_path / "wwt" / "training").exists()


def test_wwt_install_writes_units_with_one_path_per_satellite(tmp_path):
    env = _wwt_env(tmp_path)
    for sat in ("sat-0a1b2c", "sat-1d2e3f"):
        (tmp_path / "incoming" / sat).mkdir()
    (tmp_path / "incoming" / "stray-file").write_text("")  # not a directory: no path
    r = _run("tools/wakeword/wwt/install.sh", env)
    assert r.returncode == 0, r.stderr
    sd = tmp_path / "systemd"
    path = (sd / "friday-wwt-featcache.path").read_text()
    lines = [ln for ln in path.splitlines() if ln.startswith("PathChanged=")]
    assert lines == [f"PathChanged={tmp_path}/incoming/sat-0a1b2c/.sync_ok",
                     f"PathChanged={tmp_path}/incoming/sat-1d2e3f/.sync_ok"]
    assert "WantedBy=paths.target" in path
    timer = (sd / "friday-wwt-featcache.timer").read_text()
    assert "OnCalendar=*-*-* 07:00" in timer and "Persistent=true" in timer and "WantedBy=timers.target" in timer
    svc = (sd / "friday-wwt-featcache.service").read_text()
    assert "Type=oneshot" in svc and "ExecStart=/opt/friday/wwt/featcache_nightly.sh" in svc and "Nice=19" in svc
    assert "Requires=docker.service" in svc
    # a satellite paired later shows up on the next install
    (tmp_path / "incoming" / "sat-2a3b4c").mkdir()
    assert _run("tools/wakeword/wwt/install.sh", env).returncode == 0
    assert "sat-2a3b4c/.sync_ok" in (sd / "friday-wwt-featcache.path").read_text()


def test_wwt_install_without_satellites_writes_no_path_unit(tmp_path):
    env = _wwt_env(tmp_path)
    sd = tmp_path / "systemd"
    (sd / "friday-wwt-featcache.path").write_text("stale")
    r = _run("tools/wakeword/wwt/install.sh", env)
    assert r.returncode == 0, r.stderr
    assert not (sd / "friday-wwt-featcache.path").exists() and (sd / "friday-wwt-featcache.timer").exists()
