"""Satellite root helper friday-ota-apply and migration 0001 (plan 2026-11-10 Task 13), run as real processes in a
fake root (FRIDAY_OTA_ROOT) with fake systemctl/systemd-run/visudo/chown (FRIDAY_OTA_FAKE_BIN)."""
import http.server
import io
import json
import os
import subprocess
import sys
import tarfile
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
HELPER = ROOT / "agents" / "satellite" / "friday-ota-apply"
MIGRATION = ROOT / "satellite" / "migrations" / "0001_versioned_layout.sh"


def _add(t, name, data: bytes, mode=0o644):
    info = tarfile.TarInfo(name)
    info.size, info.mode = len(data), mode
    t.addfile(info, io.BytesIO(data))


def package(path: Path, tag: str, lock="L1", migrations=None, system=None, sha="s1"):
    with tarfile.open(path, "w:gz") as t:
        _add(t, "program/friday_satelite.py", b"# program\n")
        _add(t, "program/sons/onboarding/ready.wav", b"RIFF")
        _add(t, "agent/friday_satellite_agent/__main__.py", b"# agent\n")
        _add(t, "agent/friday_agent_core/__init__.py", b"")
        _add(t, "agent/friday_agent_core/ota/__init__.py", b"")
        _add(t, "agent/friday_agent_core/ota/migrations.py",
             (ROOT / "agents/common/friday_agent_core/ota/migrations.py").read_bytes())
        _add(t, "program/requirements.lock", b"")
        _add(t, "agent/requirements.lock", b"")
        _add(t, "manifest.json", json.dumps({"tag": tag, "sha": sha, "lock_sha256": lock}).encode())
        for name, body in (migrations or {}).items():
            _add(t, f"migrations/{name}", body.encode())
        for name, body in (system or {"friday-satelite.service": "[Service]\n"}).items():
            _add(t, f"system/{name}", body.encode())
    return path


class Health(http.server.BaseHTTPRequestHandler):
    status = {"estado": "parada", "palavras_carregadas": ["friday"]}

    def do_GET(self):
        body = (json.dumps(Health.status) if isinstance(Health.status, dict) else Health.status).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


@pytest.fixture
def env(tmp_path):
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Health)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    Health.status = {"estado": "parada", "palavras_carregadas": ["friday"]}
    fake = tmp_path / "fakebin"
    fake.mkdir()
    log = tmp_path / "calls.log"
    for name, body in {
        "systemctl": f'echo "systemctl $*" >> {log}; case "$*" in *friday-ota-activate*) exit 3 ;; esac; exit 0',
        "chown": "exit 0",
        "visudo": f'echo "visudo $*" >> {log}; [ -f {tmp_path}/visudo_fails ] && exit 1; exit 0',
        # detaches like systemd-run: drops its own options, exports --setenv, runs the rest in the background
        "systemd-run": f'echo "systemd-run $*" >> {log}\n'
                       'while [ $# -gt 0 ]; do case "$1" in --setenv=*) export "${1#--setenv=}"; shift ;; '
                       '--unit) shift 2 ;; --*) shift ;; *) break ;; esac; done\n'
                       'nohup "$@" >/dev/null 2>&1 &\nexit 0',
    }.items():
        (fake / name).write_text("#!/bin/sh\n" + body + "\n")
        (fake / name).chmod(0o755)
    root = tmp_path / "root"
    (root / "var/lib/friday/ota/downloads").mkdir(parents=True)
    (root / "opt/friday/releases").mkdir(parents=True)
    e = {**os.environ, "FRIDAY_OTA_ROOT": str(root), "FRIDAY_OTA_FAKE_BIN": str(fake),
         "FRIDAY_OTA_STATUS_URL": f"http://127.0.0.1:{srv.server_port}/status",
         "FRIDAY_OTA_AGENT_PORT": str(srv.server_port), "FRIDAY_OTA_HEALTH_TIMEOUT": "3",
         "FRIDAY_OTA_AGENT_STABLE_S": "0"}
    yield {"root": root, "env": e, "log": log, "tmp": tmp_path, "dl": root / "var/lib/friday/ota/downloads"}
    srv.shutdown()


def helper(env, *args, detached=False):
    e = dict(env["env"], **({"FRIDAY_OTA_DETACHED": "1"} if detached else {}))
    return subprocess.run([sys.executable, str(HELPER), *args], env=e, capture_output=True, text=True, timeout=120)


def state(env):
    return json.loads((env["root"] / "var/lib/friday/ota.json").read_text())


def current(env):
    return os.readlink(env["root"] / "opt/friday/current")


def staged(env, tag, **kw):
    pkg = package(env["dl"] / f"{tag}.tar.gz", tag, **kw)
    r = helper(env, "stage", str(pkg), tag)
    assert r.returncode == 0, r.stdout + r.stderr
    return env["root"] / "opt/friday/releases" / tag


def installed_current(env, tag="v0.1.0", lock="L1"):
    """A satellite already on the versioned layout, running `tag`."""
    rel = env["root"] / "opt/friday/releases" / tag
    (rel / "program").mkdir(parents=True)
    (rel / "manifest.json").write_text(json.dumps({"tag": tag, "lock_sha256": lock}))
    subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(rel / "venv")], check=True)
    sp = next((rel / "venv/lib").glob("python3*/site-packages"))
    (sp / "marker_pkg.py").write_text("X = 1\n")
    (rel / ".complete").touch()
    os.symlink(f"releases/{tag}", env["root"] / "opt/friday/current")
    return rel


def test_rejects_bad_tag_and_paths(env):
    pkg = package(env["dl"] / "p.tar.gz", "v0.2.0")
    assert helper(env, "stage", str(pkg), "v1").returncode == 2
    assert helper(env, "stage", str(pkg), "../../etc").returncode == 2
    outside = package(env["tmp"] / "p.tar.gz", "v0.2.0")
    assert helper(env, "stage", str(outside), "v0.2.0").returncode == 2
    assert helper(env, "activate", "v0.2.0;rm").returncode == 2
    assert helper(env, "activate", "v9.9.9").returncode == 2  # never staged
    assert not list((env["root"] / "opt/friday/releases").iterdir())


def test_stage_failure_leaves_current_untouched(env):
    installed_current(env)
    wrong = package(env["dl"] / "w.tar.gz", "v0.3.0")  # manifest says another tag
    assert helper(env, "stage", str(wrong), "v0.2.0").returncode == 2
    new_lock = package(env["dl"] / "n.tar.gz", "v0.2.0", lock="L2")  # requirements changed, no wheels
    r = helper(env, "stage", str(new_lock), "v0.2.0")
    assert r.returncode == 2 and "wheels" in r.stdout
    assert sorted(p.name for p in (env["root"] / "opt/friday/releases").iterdir()) == ["v0.1.0"]
    assert current(env) == "releases/v0.1.0"


def test_stage_same_lock_hardlinks_the_running_venv(env):
    old = installed_current(env)
    rel = staged(env, "v0.2.0")
    a = next((old / "venv/lib").glob("python3*/site-packages")) / "marker_pkg.py"
    b = next((rel / "venv/lib").glob("python3*/site-packages")) / "marker_pkg.py"
    assert b.exists() and a.stat().st_ino == b.stat().st_ino
    assert (rel / ".complete").exists() and current(env) == "releases/v0.1.0"


def test_activate_survives_caller_exit(env):
    installed_current(env)
    staged(env, "v0.2.0")
    t0 = time.time()
    r = helper(env, "activate", "v0.2.0")  # the caller (the agent) is restarted: the helper must not need it
    assert r.returncode == 0 and time.time() - t0 < 10
    assert "systemd-run --unit friday-ota-activate --collect --no-block" in env["log"].read_text()
    for _ in range(100):
        p = env["root"] / "var/lib/friday/ota.json"
        if p.exists() and state(env)["state"] != "activating":
            break
        time.sleep(0.1)
    assert state(env)["state"] == "installed" and state(env)["previous"] == "v0.1.0"
    assert current(env) == "releases/v0.2.0"
    assert (env["root"] / "etc/systemd/system/friday-satelite.service").exists()
    assert (env["root"] / "home/jose/satelite/sons/onboarding/ready.wav").exists()
    assert "systemctl restart friday-satelite friday-satellite-agent" in env["log"].read_text()


def test_unhealthy_rolls_back_and_records_stage(env):
    installed_current(env)
    staged(env, "v0.2.0")
    Health.status = {"estado": "parada", "palavras_carregadas": []}
    assert helper(env, "activate", "v0.2.0", detached=True).returncode == 2
    s = state(env)
    assert (s["state"], s["stage"], s["error"]) == ("rolled_back", "health", "no wake word loaded")
    assert current(env) == "releases/v0.1.0"


def test_migrations_run_once_before_switch(env):
    installed_current(env)
    seen = env["tmp"] / "seen"
    mig = {"0001_probe.sh": f'readlink "$FRIDAY_OTA_ROOT/opt/friday/current" >> {seen}; echo "$FRIDAY_TAG" >> {seen}'}
    staged(env, "v0.2.0", migrations=mig)
    assert helper(env, "activate", "v0.2.0", detached=True).returncode == 0
    staged(env, "v0.3.0", migrations=mig)
    assert helper(env, "activate", "v0.3.0", detached=True).returncode == 0
    assert seen.read_text().split() == ["releases/v0.1.0", "v0.2.0"]  # ran once, before the switch
    assert "0001_probe.sh" in json.loads((env["root"] / "var/lib/friday/migrations.json").read_text())


def test_failed_migration_switches_nothing(env):
    installed_current(env)
    staged(env, "v0.2.0", migrations={"0001_bad.sh": "exit 3"})
    assert helper(env, "activate", "v0.2.0", detached=True).returncode == 2
    assert (state(env)["state"], state(env)["stage"]) == ("failed", "migrations")
    assert current(env) == "releases/v0.1.0"


def test_sudoers_checked_before_install(env):
    installed_current(env)
    staged(env, "v0.2.0", system={"sudoers.friday-satellite-agent": "garbage", "friday-satelite.service": "x"})
    (env["tmp"] / "visudo_fails").touch()
    assert helper(env, "activate", "v0.2.0", detached=True).returncode == 2
    assert (state(env)["state"], state(env)["stage"]) == ("failed", "system")
    assert not (env["root"] / "etc/sudoers.d/friday-satellite-agent").exists()
    assert not (env["root"] / "etc/systemd/system/friday-satelite.service").exists()
    assert current(env) == "releases/v0.1.0"


def test_prune_keeps_current_and_previous(env):
    rels = env["root"] / "opt/friday/releases"
    for t in ("v0.0.0-legacy", "v0.1.0", "v0.2.0", "v0.3.0", "v0.4.0"):
        (rels / t).mkdir()
    (rels / ".v0.5.0.new").mkdir()
    os.symlink("releases/v0.1.0", env["root"] / "opt/friday/current")
    (env["root"] / "var/lib/friday/ota.json").write_text(json.dumps({"previous": "v0.0.0-legacy"}))
    assert helper(env, "prune").returncode == 0
    assert sorted(p.name for p in rels.iterdir()) == [".v0.5.0.new", "v0.0.0-legacy", "v0.1.0"]


def test_migration_0001_records_the_legacy_release_once(env):
    root = env["root"]
    prog, agent = root / "home/jose/satelite", root / "opt/friday-satellite-agent"
    (prog / "sons").mkdir(parents=True)
    (prog / "friday_satelite.py").write_text("# old\n")
    (prog / "FRIDAY_VERSION.pending").write_text("v0.2.1 abc\n")
    (agent / "friday_satellite_agent").mkdir(parents=True)
    (agent / "friday_agent_core").mkdir()
    e = dict(env["env"])
    assert subprocess.run(["bash", str(MIGRATION)], env=e).returncode == 0
    leg = root / "opt/friday/releases/v0.0.0-legacy"
    assert current(env) == "releases/v0.0.0-legacy"
    assert (leg / "program/friday_satelite.py").exists() and (leg / "program/FRIDAY_VERSION").read_text() == "v0.2.1 abc\n"
    assert os.readlink(leg / "venv") == "/home/jose/oww" and (leg / "agent/friday_agent_core").is_dir()
    assert (prog / "sons").is_dir() and (prog / "friday_satelite.py").exists()  # data and old code stay put
    (leg / "program/friday_satelite.py").write_text("# touched\n")
    assert subprocess.run(["bash", str(MIGRATION)], env=e).returncode == 0  # idempotent
    assert (leg / "program/friday_satelite.py").read_text() == "# touched\n"


# ---- review round 1 (C1, I2, I3, M1–M4)
def _wheels(env, tag):
    """A wheels tarball whose only content pip will never need (the fake pip below ignores it)."""
    w = env["dl"] / f"{tag}-wheels.tar.gz"
    with tarfile.open(w, "w:gz") as t:
        _add(t, "README", b"x")
    return w


def test_legacy_stage_carries_the_openwakeword_models_from_oww(env):
    oww_sp = env["root"] / "home/jose/oww/lib/python3.11/site-packages"
    (oww_sp / "openwakeword/resources/models").mkdir(parents=True)
    (oww_sp / "openwakeword/resources/models/melspectrogram.onnx").write_bytes(b"M")
    pkg = package(env["dl"] / "v0.3.0.tar.gz", "v0.3.0", lock="NEW")
    # pip is the one from the fresh venv: make it a no-op that creates the openwakeword package folder
    e = dict(env["env"], FRIDAY_OTA_FAKE_PIP="1")
    r = subprocess.run([sys.executable, str(HELPER), "stage", str(pkg), "v0.3.0", "--wheels", str(_wheels(env, "v0.3.0"))],
                       env=e, capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr
    sp = next((env["root"] / "opt/friday/releases/v0.3.0/venv/lib").glob("python3*/site-packages"))
    assert (sp / "openwakeword/resources/models/melspectrogram.onnx").read_bytes() == b"M"


def test_a_crash_in_the_health_check_still_rolls_back(env):
    installed_current(env)
    staged(env, "v0.2.0")
    Health.status = "not a dict"  # the program answers garbage: _program_ok must not raise out
    assert helper(env, "activate", "v0.2.0", detached=True).returncode == 2
    assert state(env)["state"] == "rolled_back" and current(env) == "releases/v0.1.0"


def test_recover_rolls_back_an_activation_cut_by_a_reboot(env):
    installed_current(env)
    staged(env, "v0.2.0")
    os.remove(env["root"] / "opt/friday/current")
    os.symlink("releases/v0.2.0", env["root"] / "opt/friday/current")  # switched, then the power went
    (env["root"] / "var/lib/friday/ota.json").write_text(json.dumps(
        {"tag": "v0.2.0", "previous": "v0.1.0", "state": "activating", "at": 1}))
    assert helper(env, "recover", detached=True).returncode == 0
    s = state(env)
    assert (s["state"], s["stage"]) == ("rolled_back", "interrupted") and current(env) == "releases/v0.1.0"
    assert helper(env, "recover", detached=True).returncode == 0 and state(env)["state"] == "rolled_back"


def test_rollback_writes_the_state_before_restarting(env):
    installed_current(env)
    staged(env, "v0.2.0")
    assert helper(env, "activate", "v0.2.0", detached=True).returncode == 0
    log_before = env["log"].read_text()
    assert helper(env, "rollback", detached=True).returncode == 0
    assert state(env)["state"] == "rolled_back" and current(env) == "releases/v0.1.0"
    assert "systemctl restart" in env["log"].read_text()[len(log_before):]


def test_activating_the_current_release_keeps_previous(env):
    installed_current(env)
    staged(env, "v0.2.0")
    assert helper(env, "activate", "v0.2.0", detached=True).returncode == 0
    assert helper(env, "activate", "v0.2.0", detached=True).returncode == 0
    assert state(env)["previous"] == "v0.1.0"


def test_prune_keeps_only_current_and_previous_and_never_a_stage_in_progress(env):
    rels = env["root"] / "opt/friday/releases"
    for t in ("v0.1.0", "v0.2.0", "v0.3.0", "v0.4.0"):
        (rels / t).mkdir()
    (rels / ".v0.5.0.new").mkdir()
    os.symlink("releases/v0.2.0", env["root"] / "opt/friday/current")
    (env["root"] / "var/lib/friday/ota.json").write_text(json.dumps({"previous": "v0.1.0"}))
    assert helper(env, "prune").returncode == 0
    assert sorted(p.name for p in rels.iterdir()) == [".v0.5.0.new", "v0.1.0", "v0.2.0"]


def test_a_different_package_under_a_staged_tag_is_staged_again(env):
    installed_current(env)
    staged(env, "v0.2.0")
    (env["root"] / "opt/friday/releases/v0.2.0/program/marker").write_text("old")
    pkg = package(env["dl"] / "v0.2.0b.tar.gz", "v0.2.0", sha="other")
    assert helper(env, "stage", str(pkg), "v0.2.0").returncode == 0
    assert not (env["root"] / "opt/friday/releases/v0.2.0/program/marker").exists()
