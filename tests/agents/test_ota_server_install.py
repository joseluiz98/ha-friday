"""Server-side OTA install and rollback (plan 2026-11-10 Task 10)."""
import io
import json
import os
import subprocess
import sys
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "agents" / "server"))
sys.path.insert(0, str(ROOT / "agents" / "common"))
from friday_agent_core.ota import manifest as mf  # noqa: E402
from friday_server_agent.ota import server_install as si  # noqa: E402
from friday_server_agent.ota import store as st  # noqa: E402

OLD = "a" * 40
NEW = "b" * 40
MANIFEST = mf.parse(json.loads((ROOT / "ota" / "release.json").read_text()))
VERSIONED = ("server-agent", "server", "voice-gateway", "wwt")


def _tar() -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as t:
        for name, data in {"ota/components.sh": (ROOT / "ota" / "components.sh").read_bytes(),
                           "brain/skills/s1/SKILL.md": b"x", "server/systemd/friday-x.timer": b"x",
                           "ota/self_update.sh": b"#!/bin/sh\n"}.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            t.addfile(info, io.BytesIO(data))
    return buf.getvalue()


class Repo:
    def __init__(self, changed):
        self.changed = changed

    def changed_paths(self, old, new):
        if self.changed is Exception:
            raise RuntimeError("unknown sha")
        return list(self.changed)

    def archive(self, sha, paths):
        return _tar()


class Snaps:
    def __init__(self, world, refuse=False):
        self.w, self.refuse, self.calls, self.desc = world, refuse, [], ""

    def snapshot_name(self, tag):
        return f"friday-ota-{tag}"

    def list(self):
        return [{"name": "friday-ota-v0.1.0", "snaptime": 1}]

    def create(self, name):
        self.calls.append(("create", name, self.w.installs_so_far()))
        return "UPID:c"

    def delete(self, name):
        self.calls.append(("delete", name))
        return "UPID:d"

    def wait(self, upid, timeout=900):
        pass

    def annotate(self, name, text):
        self.desc = text
        self.calls.append(("annotate", name, text))

    def rollback(self, name, start=False):
        # what the disk will hold after the rollback = the state saved right now
        self.calls.append(("rollback", name, start, json.loads(Path(self.w.store.path).read_text())))
        if self.refuse:
            raise RuntimeError("403")
        return "UPID:r"

    def describe(self, name):
        return self.desc

    def prune(self, keep_s):
        return []


class World:
    def __init__(self, tmp: Path, changed=("voice_gateway/bot.py",), busy=False, restart_code=202, ready=True,
                 fail_install=(), image_label=OLD, snapshots=True, refuse=False):
        self.tmp, self.busy_flag, self.restart_code, self.ready = tmp, busy, restart_code, ready
        self.fail_install, self.label, self.image = set(fail_install), image_label, "friday:v0.1.0"
        self.cmds, self.now, self.self_running = [], 1_000_000.0, False
        self.paths = {"root": str(tmp / "ota"), "activity": str(tmp / "activity.json"),
                      "image_dir": str(tmp / "image"), "workspace": str(tmp / "ws"),
                      "status_url": "http://status"}
        for c in VERSIONED:
            p = tmp / "v" / c / "FRIDAY_VERSION"
            p.parent.mkdir(parents=True)
            p.write_text(f"v0.1.0 {OLD}\n")
            self.paths[f"version:{c}"] = str(p)
        (tmp / "ws" / "skills" / "s1").mkdir(parents=True)
        (tmp / "ws" / "skills" / "s1" / "FRIDAY_VERSION").write_text(f"v0.1.0 {OLD}\n")
        (tmp / "image").mkdir()
        self.write_activity()
        self.store = st.OtaStore(str(tmp / "state.json"), str(tmp / "events.ndjson"), clock=lambda: self.now)
        self.snaps = Snaps(self, refuse) if snapshots else None
        self.inst = si.ServerInstaller(self.store, Repo(changed), self.snaps, lambda: "tok", run=self.run,
                                       http=self.http, log_tail=lambda: "", clock=lambda: self.now,
                                       sleep=self.sleep, paths=self.paths)

    def write_activity(self):
        Path(self.paths["activity"]).write_text(json.dumps(
            {"updated_at": self.now, "sessions": ["s"] if self.busy_flag else [], "brain_turns": 0}))

    def sleep(self, s):
        self.now += s

    def installs_so_far(self):
        return [c[1:] for c in self.cmds if c[0] == "install"]

    def run(self, cmd, **kw):
        ok = subprocess.CompletedProcess(cmd, 0, "", "")
        if cmd[:2] == ["bash", "-c"]:
            comp, line = cmd[4], kw["env"]["FRIDAY_VERSION_LINE"]
            self.cmds.append(("install", comp, line))
            if (comp, line.split()[0]) in self.fail_install or comp in self.fail_install:
                return subprocess.CompletedProcess(cmd, 1, "", "boom")
            if comp in VERSIONED:
                Path(self.paths[f"version:{comp}"]).write_text(line + "\n")
            elif comp == "brain-skills":
                (self.tmp / "ws" / "skills" / "s1" / "FRIDAY_VERSION").write_text(line + "\n")
            elif comp == "container":
                self.built = line.split()[0]
            return ok
        self.cmds.append(tuple(cmd))
        if cmd[:2] == ["systemctl", "is-active"] and "friday-ota-self" in cmd:
            return subprocess.CompletedProcess(cmd, 0 if self.self_running else 3, "", "")
        if cmd[:2] == ["docker", "inspect"]:
            if cmd[2] == "friday":
                return subprocess.CompletedProcess(cmd, 0, self.image + "\n", "")
            return subprocess.CompletedProcess(cmd, 0, (self.label or "") + "\n", "")
        if cmd[:2] == ["docker", "logs"]:
            return subprocess.CompletedProcess(cmd, 0, "pronto: abra\n", None)
        if cmd[:3] == ["docker", "compose", "up"]:
            tag = (Path(self.paths["image_dir"]) / ".env").read_text().strip().partition("=")[2]
            self.image = f"friday:{tag}"
            return ok
        if cmd[:2] == ["docker", "images"]:
            return subprocess.CompletedProcess(cmd, 0, "v0.3.0\nv0.2.0\nv0.1.0\n", "")
        return ok

    def http(self, method, url, token="", timeout=10):
        self.cmds.append(("http", method, url.rsplit("/", 1)[-1]))
        if method == "POST":
            return self.restart_code, None
        return 200, {"ready": self.ready}

    def installs(self):
        return [c[1:] for c in self.cmds if c[0] == "install"]


def test_plan_installs_only_components_whose_code_changed(tmp_path):
    w = World(tmp_path, changed=["voice_gateway/bot.py", "brain/skills/x/SKILL.md"])
    assert w.inst.plan(NEW, MANIFEST) == ["brain-skills", "voice-gateway"]


def test_plan_reinstalls_when_history_is_unknown(tmp_path):
    w = World(tmp_path, changed=Exception, image_label="")
    assert w.inst.plan(NEW, MANIFEST) == list(si.SERVER_COMPONENTS)


def test_light_components_install_in_order_with_the_release_line(tmp_path):
    w = World(tmp_path, changed=["voice_gateway/bot.py", "brain/skills/x/SKILL.md", "server/status/a.py"])
    out = w.inst.apply("v0.3.0", NEW, MANIFEST)
    assert out["result"] == "installed" and out["installed"] == ["brain-skills", "server", "voice-gateway"]
    assert w.installs() == [(c, f"v0.3.0 {NEW}") for c in ("brain-skills", "server", "voice-gateway")]
    assert ("http", "POST", "restart") in w.cmds
    assert w.store.data["server"]["voice-gateway"] == {"installed_tag": "v0.3.0", "installed_sha": NEW}
    assert "run" not in w.store.data["server_ota"] and w.store.data["server_ota"]["integration_target"] == "v0.3.0"


def test_health_failure_rolls_back_every_touched_component_in_reverse(tmp_path):
    w = World(tmp_path, changed=["voice_gateway/bot.py", "brain/skills/x/SKILL.md"], ready=False)
    out = w.inst.apply("v0.3.0", NEW, MANIFEST)
    assert out["result"] == "rolled_back" and out["stage"] == "health" and out["component"] == "voice-gateway"
    assert w.installs()[2:] == [("voice-gateway", f"v0.1.0 {OLD}"), ("brain-skills", f"v0.1.0 {OLD}")]
    assert Path(w.paths["version:voice-gateway"]).read_text().strip() == f"v0.1.0 {OLD}"
    assert "run" not in w.store.data["server_ota"]


def test_install_failure_rolls_back_too(tmp_path):
    w = World(tmp_path, changed=["server/status/a.py"], fail_install=[("server", "v0.3.0")])
    out = w.inst.apply("v0.3.0", NEW, MANIFEST)
    assert out["result"] == "rolled_back" and out["stage"] == "install"
    assert w.installs() == [("server", f"v0.3.0 {NEW}"), ("server", f"v0.1.0 {OLD}")]


def test_conversation_open_defers_without_touching_anything(tmp_path):
    w = World(tmp_path, busy=True)
    assert w.inst.apply("v0.3.0", NEW, MANIFEST) == {"result": "deferred", "stage": "busy"}
    assert w.installs() == []


def test_restart_refused_puts_back_only_the_voice(tmp_path):
    w = World(tmp_path, changed=["voice_gateway/bot.py", "brain/skills/x/SKILL.md"], restart_code=409)
    out = w.inst.apply("v0.3.0", NEW, MANIFEST)
    assert out["result"] == "deferred"
    assert w.installs() == [("brain-skills", f"v0.3.0 {NEW}"), ("voice-gateway", f"v0.3.0 {NEW}"),
                            ("voice-gateway", f"v0.1.0 {OLD}")]
    assert w.store.data["server"]["brain-skills"]["installed_tag"] == "v0.3.0"


def test_heavy_waits_for_continuous_idle_unless_manual(tmp_path):
    w = World(tmp_path, changed=["server/image/Dockerfile"])
    assert w.inst.apply("v0.3.0", NEW, MANIFEST) == {"result": "deferred", "stage": "heavy_idle"}
    assert w.snaps.calls == [] and w.installs() == []
    w.inst.sample_idle()
    w.now += si.HEAVY_IDLE_S + 1
    assert w.inst.apply("v0.3.0", NEW, MANIFEST)["result"] == "installed"
    w2 = World(tmp_path / "m", changed=["server/image/Dockerfile"])
    assert w2.inst.apply("v0.3.0", NEW, MANIFEST, manual=True)["result"] == "installed"


def test_container_snapshot_before_build_then_switch_and_prune(tmp_path):
    w = World(tmp_path, changed=["server/image/Dockerfile"])
    out = w.inst.apply("v0.3.0", NEW, MANIFEST, manual=True)
    assert out["result"] == "installed"
    assert ("delete", "friday-ota-v0.1.0") in w.snaps.calls  # only the last update's snapshot is kept
    create = [c for c in w.snaps.calls if c[0] == "create"][0]
    assert create[2] == []  # nothing built yet when the snapshot is taken
    assert w.image == "friday:v0.3.0"
    assert (Path(w.paths["image_dir"]) / ".env").read_text() == "FRIDAY_IMAGE_TAG=v0.3.0\n"
    assert ("docker", "rmi", "friday:v0.2.0") in w.cmds and ("docker", "rmi", "friday:v0.1.0") not in w.cmds


def test_container_health_failure_restores_the_snapshot_with_the_run_saved_first(tmp_path):
    w = World(tmp_path, changed=["server/image/Dockerfile"], ready=False)
    w.inst.apply("v0.3.0", NEW, MANIFEST, manual=True)
    kinds = [c[0] for c in w.snaps.calls]
    assert kinds.index("annotate") < kinds.index("rollback")
    rb = [c for c in w.snaps.calls if c[0] == "rollback"][0]
    assert rb[2] is True and rb[3]["server_ota"]["run"]["stage"] == "rolling_back"
    assert w.snaps.desc.startswith("friday-ota rolling_back health")


def test_snapshot_refused_switches_back_to_the_previous_image(tmp_path):
    w = World(tmp_path, changed=["server/image/Dockerfile"], ready=False, refuse=True)
    out = w.inst.apply("v0.3.0", NEW, MANIFEST, manual=True)
    assert out["result"] == "rolled_back" and out["component"] == "container"
    assert w.image == "friday:v0.1.0"


def test_build_failure_does_not_restore_the_vm(tmp_path):
    w = World(tmp_path, changed=["server/image/Dockerfile"], fail_install=["container"])
    out = w.inst.apply("v0.3.0", NEW, MANIFEST, manual=True)
    assert out["stage"] == "install" and "rollback" not in [c[0] for c in w.snaps.calls]
    assert w.image == "friday:v0.1.0"


def test_heavy_without_proxmox_token_fails_before_any_change(tmp_path):
    w = World(tmp_path, changed=["server/image/Dockerfile"], snapshots=False)
    out = w.inst.apply("v0.3.0", NEW, MANIFEST, manual=True)
    assert out["stage"] == "snapshot" and w.installs() == []


def test_recover_after_a_snapshot_rollback_reports_and_undoes_the_run(tmp_path):
    w = World(tmp_path)
    Path(w.paths["version:server"]).write_text(f"v0.3.0 {NEW}\n")  # the run had installed `server`
    w.store.data["server_ota"] = {"run": {"tag": "v0.3.0", "sha": NEW, "work": str(tmp_path / "work"),
                                          "done": ["server"], "component": "container", "stage": "snapshot",
                                          "prev": {"server": f"v0.1.0 {OLD}"}, "prev_image": "friday:v0.1.0",
                                          "snapshot": "friday-ota-v0.3.0"}}
    w.snaps.desc = "friday-ota rolling_back health: container not healthy in 180s"
    out = w.inst.recover()
    assert out["result"] == "rolled_back" and out["stage"] == "health" and out["tag"] == "v0.3.0"
    assert w.installs() == [("server", f"v0.1.0 {OLD}")]
    assert "rollback" not in [c[0] for c in w.snaps.calls]  # never a second rollback
    assert "run" not in w.store.data["server_ota"]
    assert w.inst.recover() is None


def test_server_agent_goes_last_through_systemd_run(tmp_path):
    w = World(tmp_path, changed=["voice_gateway/bot.py", "agents/server/x.py"])
    out = w.inst.apply("v0.3.0", NEW, MANIFEST)
    assert out["result"] == "handed_off" and out["installed"] == ["voice-gateway"]
    run = [c for c in w.cmds if c[0] == "systemd-run"][0]
    assert run[1:4] == ("--unit", "friday-ota-self", "--collect") and run[-2].endswith("self.json")
    assert run.index(f"v0.3.0 {NEW}") < run.index(f"v0.1.0 {OLD}")
    assert "run" not in w.store.data["server_ota"]  # the agent's own restart is not an interruption


def test_absorb_self_result(tmp_path):
    w = World(tmp_path)
    Path(w.paths["root"]).mkdir(parents=True, exist_ok=True)
    Path(w.paths["root"], "self.json").write_text('{"tag": "v0.3.0", "result": "installed", "stage": ""}')
    out = w.inst.absorb_self_result()
    assert out["result"] == "installed" and out["component"] == "server-agent"
    assert w.store.data["server"]["server-agent"]["installed_tag"] == "v0.3.0"
    assert w.inst.absorb_self_result() is None


def test_components_recipe_covers_every_server_component():
    for comp in si.SERVER_COMPONENTS:
        r = subprocess.run(["bash", "-c", '. ota/components.sh && friday_component "$1" && echo "$CMD"', "x", comp],
                           cwd=ROOT, capture_output=True, text=True)
        assert r.returncode == 0 and r.stdout.startswith("bash "), comp
    assert subprocess.run(["bash", "-c", ". ota/components.sh && friday_component nope"], cwd=ROOT).returncode == 1


def test_nothing_starts_while_the_agent_replaces_itself(tmp_path):
    w = World(tmp_path)
    w.self_running = True
    assert w.inst.apply("v0.3.0", NEW, MANIFEST) == {"result": "deferred", "stage": "self_update"}


# ---- the orchestrator drives the installer toward the server channel's target
from tests.agents.test_ota_orchestrator import World as OrchWorld, man, rel  # noqa: E402


class FakeInstaller:
    def __init__(self, results):
        self.results, self.calls, self.recovered = list(results), [], 0

    def recover(self):
        self.recovered += 1
        return None

    def absorb_self_result(self):
        return None

    def prune_snapshots(self):
        return []

    def apply(self, tag, sha, manifest, manual=False):
        self.calls.append((tag, manual))
        return self.results.pop(0) if self.results else {"result": "installed", "installed": []}


def _orch(tmp_path, releases, results=(), alpha=False):
    w = OrchWorld(tmp_path, releases, {r.release.sha: man(server_proto=2) for r in releases})
    w.orch.installer = FakeInstaller(results)
    if alpha:
        w.store.device("sat-00aa22")["channel"] = "alpha"
    return w


def test_server_follows_alpha_when_an_alpha_device_exists(tmp_path):
    w = _orch(tmp_path, [rel("v0.3.0"), rel("v0.4.0", pre=True)], alpha=True)
    w.orch.poll()
    assert w.orch.installer.calls == [("v0.4.0", False)] and w.orch.installer.recovered == 1
    assert w.store.data["server_ota"]["installed_tag"] == "v0.4.0" and w.store.data["server_protocol"] == 2
    w.orch.poll()
    assert len(w.orch.installer.calls) == 1 and w.orch.installer.recovered == 1


def test_server_rollback_on_alpha_fails_the_soak(tmp_path):
    fail = {"result": "rolled_back", "component": "container", "stage": "health", "error": "x"}
    w = _orch(tmp_path, [rel("v0.3.0"), rel("v0.4.0", pre=True)], results=[fail], alpha=True)
    w.orch.poll()
    assert w.orch.installer.calls == [("v0.4.0", False)]
    assert w.store.data["soak"]["v0.4.0"]["failed"] is True


def test_server_failures_back_off_and_push_once_when_exhausted(tmp_path):
    fail = {"result": "failed", "component": "voice-gateway", "stage": "install", "error": "x"}
    w = _orch(tmp_path, [rel("v0.3.0")], results=[fail] * 6)
    w.orch.poll()
    w.orch.poll()  # inside the first back-off: no new try
    assert len(w.orch.installer.calls) == 1
    for _ in range(5):
        w.now += 90000
        w.orch.poll()
    so = w.store.data["server_ota"]
    assert so["attempts"]["exhausted"] and len(w.orch.installer.calls) == 5
    assert len(w.pushed) == 1 and "v0.3.0" in w.pushed[0] and "servidor" in w.pushed[0]


def test_deferred_is_not_an_attempt_and_manual_install_resets(tmp_path):
    w = _orch(tmp_path, [rel("v0.3.0")], results=[{"result": "deferred", "stage": "busy"}])
    w.orch.poll()
    assert "attempts" not in w.store.data["server_ota"] or not w.store.data["server_ota"]["attempts"]
    w.store.data["server_ota"]["attempts"] = {"version": "v0.3.0", "failures": 5, "exhausted": True}
    w.orch.install_server()
    w.orch.poll()
    assert w.orch.installer.calls[-1] == ("v0.3.0", True)
    assert w.store.data["server_ota"]["install_requested"] is False


def test_server_never_goes_back_by_itself(tmp_path):
    w = _orch(tmp_path, [rel("v0.3.0")])
    w.store.data["server_ota"]["installed_tag"] = "v0.4.0"  # an alpha device left the channel
    w.orch.poll()
    assert w.orch.installer.calls == []
