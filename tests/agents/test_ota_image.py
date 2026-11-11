"""Provisioning image built on the VM (plan 2026-11-10 Task 12)."""
import fcntl
import io
import json
import subprocess
import sys
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "agents" / "server"))
from friday_server_agent.ota import image as im  # noqa: E402

SHA = "c" * 40


def _src_tar() -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as t:
        for name in ("satellite/image/stage.sh", "satellite/image/build.sh"):
            info = tarfile.TarInfo(name)
            info.size = 1
            t.addfile(info, io.BytesIO(b"#"))
    return buf.getvalue()


class World:
    def __init__(self, tmp: Path, fail=None, unit_active=False):
        self.tmp, self.fail, self.unit_active, self.cmds = tmp, fail, unit_active, []
        self.share = tmp / "share"
        self.share.mkdir(parents=True)
        root = tmp / "ota"
        (root / "packages" / "v0.3.0").mkdir(parents=True)
        (root / "packages" / "v0.3.0" / "satellite.tar.gz").write_bytes(b"PKG")
        self.b = im.ImageBuilder(str(tmp / "state" / "image.json"), str(tmp / "state" / "image.lock"),
                                 str(self.share), str(root), run=self.run, clock=lambda: 5000.0,
                                 work=str(tmp / "work"))

    def run(self, cmd, **kw):
        self.cmds.append(cmd)
        if cmd[:2] == ["systemctl", "is-active"]:
            return subprocess.CompletedProcess(cmd, 0 if self.unit_active else 3, "", "")
        if cmd[0] == "git":
            return subprocess.CompletedProcess(cmd, 0, _src_tar(), b"")
        if cmd[0] == "bash" and cmd[1].endswith("stage.sh"):
            assert cmd[2:] == ["--repo", str(self.tmp / "ota" / "repo"), "--ref", SHA, "--local", "--package",
                               str(self.tmp / "ota" / "packages" / "v0.3.0" / "satellite.tar.gz")]
            return subprocess.CompletedProcess(cmd, 1 if self.fail == "stage" else 0, "", "payload refused")
        if cmd[0] == "bash" and cmd[1].endswith("build.sh"):
            if self.fail == "build":
                return subprocess.CompletedProcess(cmd, 1, "", "line\nbase image hash mismatch")
            assert kw["env"]["HOME"] == "/root" and Path(kw["env"]["TMPDIR"]).is_dir()
            (Path(kw["env"]["FRIDAY_IMG_SRC"]) / "payload").mkdir(parents=True, exist_ok=True)
            out = Path(kw["env"]["FRIDAY_IMG_OUT"])
            name = kw["env"]["FRIDAY_IMG_NAME"]
            for suffix in (".img.xz", ".img.xz.sha256", ".json"):
                (out / (name + suffix)).write_text("x")
            return subprocess.CompletedProcess(cmd, 0, "", "")
        if cmd[0] == "systemd-run":
            assert json.loads((self.tmp / "state" / "image.json").read_text())["status"] == "building"
            return subprocess.CompletedProcess(cmd, 1 if self.fail == "systemd" else 0, "", "")
        raise AssertionError(cmd)


def test_build_copies_the_image_to_the_share_and_records_ok(tmp_path):
    w = World(tmp_path)
    out = w.b.build("v0.3.0", SHA)
    assert out["status"] == "ok" and out["tag"] == "v0.3.0" and out["built_at"] == 5000.0
    assert sorted(p.name for p in w.share.iterdir()) == [
        "friday-satellite-v0.3.0.img.xz", "friday-satellite-v0.3.0.img.xz.sha256", "friday-satellite-v0.3.0.json"]
    assert json.loads((tmp_path / "state" / "image.json").read_text())["status"] == "ok"


def test_failed_build_records_the_error_and_keeps_the_full_log(tmp_path):
    w = World(tmp_path, fail="build")
    out = w.b.build("v0.3.0", SHA)
    assert (out["status"], out["error"]) == ("error", "build: base image hash mismatch")
    assert not list(w.share.iterdir())
    log = tmp_path / "state" / "image-build.log"
    assert "line\nbase image hash mismatch" in log.read_text() and (log.stat().st_mode & 0o777) == 0o600


def test_nothing_big_or_secret_stays_after_a_build(tmp_path):
    w = World(tmp_path)
    (tmp_path / "work" / "build").mkdir(parents=True)
    (tmp_path / "work" / "build" / "base-lite.img").write_text("3GB")
    (tmp_path / "work" / "build" / "base-lite.img.xz").write_text("cache")
    assert w.b.build("v0.3.0", SHA)["status"] == "ok"
    assert sorted(p.name for p in (tmp_path / "work").rglob("*")) == ["base-lite.img.xz", "build"]


def test_share_down_is_an_error_not_a_local_copy(tmp_path):
    w = World(tmp_path)
    w.share.rmdir()
    assert w.b.build("v0.3.0", SHA)["error"] == "share_unavailable" and not w.share.exists()


def test_systemd_refusal_restores_the_previous_state(tmp_path):
    w = World(tmp_path)
    assert w.b.build("v0.3.0", SHA)["status"] == "ok"
    w.fail = "systemd"
    assert w.b.start("v0.3.0", SHA) == {"ok": False, "error": "systemd-run"}
    assert json.loads((tmp_path / "state" / "image.json").read_text())["status"] == "ok"


def test_missing_package_is_an_error_before_any_step(tmp_path):
    w = World(tmp_path)
    out = w.b.build("v0.4.0", SHA)
    assert (out["status"], out["error"]) == ("error", "package_missing") and w.cmds == []


def test_one_build_at_a_time(tmp_path):
    w = World(tmp_path)
    (tmp_path / "state").mkdir()
    with open(tmp_path / "state" / "image.lock", "a") as held:
        fcntl.flock(held, fcntl.LOCK_EX)
        assert w.b.build("v0.3.0", SHA) == {"ok": False, "error": "busy"}
        assert w.b.start("v0.3.0", SHA) == {"ok": False, "error": "busy"}
    assert w.b.start("v0.3.0", SHA) == {"ok": True}
    w.unit_active = True  # systemd started the unit
    assert w.b.status()["status"] == "building"
    run = [c for c in w.cmds if c[0] == "systemd-run"][0]
    assert run[1:5] == ["--unit", "friday-image-build", "--collect", "--property=Nice=19"]
    assert run[-3:] == ["build", "v0.3.0", SHA]


def test_building_without_a_unit_or_lock_is_an_interrupted_build(tmp_path):
    w = World(tmp_path)
    w.b.start("v0.3.0", SHA)
    assert (w.b.status()["status"], w.b.status()["error"]) == ("error", "interrupted")
    w2 = World(tmp_path / "b", unit_active=True)
    w2.b.start("v0.3.0", SHA)  # refused: a unit is running already
    assert w2.b.start("v0.3.0", SHA)["error"] == "busy"


def test_retention_keeps_the_newest_tags(tmp_path):
    w = World(tmp_path)
    for t in ("v0.1.0", "v0.2.0", "v0.10.0"):
        for suffix in (".img.xz", ".json"):
            (w.share / f"friday-satellite-{t}{suffix}").write_text("x")
    (w.share / "notes.txt").write_text("keep")
    assert w.b.retain() == ["v0.1.0"]
    assert sorted(p.name for p in w.share.iterdir()) == [
        "friday-satellite-v0.10.0.img.xz", "friday-satellite-v0.10.0.json", "friday-satellite-v0.2.0.img.xz",
        "friday-satellite-v0.2.0.json", "notes.txt"]


def test_bad_tag_or_sha_never_reaches_systemd(tmp_path):
    w = World(tmp_path)
    assert w.b.start("v1;rm", SHA)["error"] == "tag" and w.b.start("v0.3.0", "x")["error"] == "tag"
    assert not [c for c in w.cmds if c[0] == "systemd-run"]


# ---- the orchestrator builds the image once per stable tag
from tests.agents.test_ota_orchestrator import World as OrchWorld, rel  # noqa: E402


class FakeImages:
    def __init__(self, state=None):
        self.state, self.started = state or {}, []

    def status(self):
        return dict(self.state)

    def start(self, tag, sha):
        self.started.append(tag)
        self.state = {"tag": tag, "status": "building"}
        return {"ok": True}


def test_new_stable_tag_builds_the_image_once(tmp_path):
    from tests.agents.test_ota_orchestrator import man
    rs = [rel("v0.3.0"), rel("v0.4.0", pre=True)]
    w = OrchWorld(tmp_path, rs, {r.release.sha: man() for r in rs})
    w.orch.images = FakeImages({"tag": "v0.2.0", "status": "ok"})
    w.orch.poll()
    assert w.orch.images.started == ["v0.3.0"]  # the stable target, never the pre-release
    w.orch.poll()
    assert w.orch.images.started == ["v0.3.0"]


def test_an_image_error_waits_for_the_button(tmp_path):
    from tests.agents.test_ota_orchestrator import man
    w = OrchWorld(tmp_path, [rel("v0.3.0")], {rel("v0.3.0").release.sha: man()})
    w.orch.images = FakeImages({"tag": "v0.3.0", "status": "error", "error": "build: x"})
    w.store.data["server_ota"]["image_auto"] = "v0.3.0"  # the automatic try already happened
    w.orch.poll()
    assert w.orch.images.started == []
    assert w.orch.image_build()["ok"] and w.orch.images.started == ["v0.3.0"]



def test_a_manual_build_of_another_tag_does_not_rearm_the_automatic_one(tmp_path):
    from tests.agents.test_ota_orchestrator import man
    w = OrchWorld(tmp_path, [rel("v0.3.0")], {rel("v0.3.0").release.sha: man()})
    w.orch.images = FakeImages({"tag": "v0.2.0", "status": "ok"})
    w.orch.poll()
    assert w.orch.images.started == ["v0.3.0"]
    w.orch.images.state = {"tag": "v0.4.0", "status": "ok"}  # another tag built by hand
    w.orch.poll()
    assert w.orch.images.started == ["v0.3.0"]


def test_pre_ota_release_gets_no_automatic_image(tmp_path):
    w = OrchWorld(tmp_path, [rel("v0.2.1")], {})  # no ota/release.json at that sha
    w.orch.images = FakeImages({})
    w.orch.poll()
    assert w.orch.images.started == []


def test_server_install_waits_for_the_image_build_and_vice_versa(tmp_path):
    from tests.agents.test_ota_orchestrator import man
    from tests.agents.test_ota_server_install import FakeInstaller
    w = OrchWorld(tmp_path, [rel("v0.3.0")], {rel("v0.3.0").release.sha: man()})
    w.orch.installer = FakeInstaller([])
    w.orch.images = FakeImages({"tag": "v0.1.0", "status": "building"})
    w.orch.poll()
    assert w.orch.installer.calls == []
    w.orch.images.state = {"tag": "v0.1.0", "status": "ok"}
    w.store.data["server_ota"]["run"] = {"tag": "v0.3.0", "stage": "install"}
    assert w.orch.image_build("v0.3.0") == {"ok": False, "error": "server_busy"}
