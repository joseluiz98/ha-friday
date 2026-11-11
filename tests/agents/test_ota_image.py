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
        root = tmp / "ota"
        (root / "packages" / "v0.3.0").mkdir(parents=True)
        (root / "packages" / "v0.3.0" / "satellite.tar.gz").write_bytes(b"PKG")
        self.b = im.ImageBuilder(str(tmp / "state" / "image.json"), str(tmp / "state" / "image.lock"),
                                 str(self.share), str(root), run=self.run, clock=lambda: 5000.0)

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
            out = Path(kw["env"]["FRIDAY_IMG_OUT"])
            name = kw["env"]["FRIDAY_IMG_NAME"]
            for suffix in (".img.xz", ".img.xz.sha256", ".json"):
                (out / (name + suffix)).write_text("x")
            return subprocess.CompletedProcess(cmd, 0, "", "")
        if cmd[0] == "systemd-run":
            return subprocess.CompletedProcess(cmd, 0, "", "")
        raise AssertionError(cmd)


def test_build_copies_the_image_to_the_share_and_records_ok(tmp_path):
    w = World(tmp_path)
    out = w.b.build("v0.3.0", SHA)
    assert out["status"] == "ok" and out["tag"] == "v0.3.0" and out["built_at"] == 5000.0
    assert sorted(p.name for p in w.share.iterdir()) == [
        "friday-satellite-v0.3.0.img.xz", "friday-satellite-v0.3.0.img.xz.sha256", "friday-satellite-v0.3.0.json"]
    assert json.loads((tmp_path / "state" / "image.json").read_text())["status"] == "ok"


def test_failed_build_records_the_error(tmp_path):
    w = World(tmp_path, fail="build")
    out = w.b.build("v0.3.0", SHA)
    assert (out["status"], out["error"]) == ("error", "build: base image hash mismatch")
    assert not w.share.exists() or not list(w.share.iterdir())


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
    w.share.mkdir()
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
    w = OrchWorld(tmp_path, [rel("v0.3.0"), rel("v0.4.0", pre=True)])
    w.orch.images = FakeImages({"tag": "v0.2.0", "status": "ok"})
    w.orch.poll()
    assert w.orch.images.started == ["v0.3.0"]  # the stable target, never the pre-release
    w.orch.poll()
    assert w.orch.images.started == ["v0.3.0"]


def test_an_image_error_waits_for_the_button(tmp_path):
    w = OrchWorld(tmp_path, [rel("v0.3.0")])
    w.orch.images = FakeImages({"tag": "v0.3.0", "status": "error", "error": "build: x"})
    w.orch.poll()
    assert w.orch.images.started == []
    assert w.orch.image_build()["ok"] and w.orch.images.started == ["v0.3.0"]
