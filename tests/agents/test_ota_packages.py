"""OTA packages (plan 2026-11-10 Task 7): per-release satellite package, wheels cache, VM repo, retention."""
import hashlib
import io
import json
import os
import sys
import tarfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "agents" / "server"))
from friday_server_agent import provisioning as pv  # noqa: E402
from friday_server_agent.ota import packages as pk, repo as rp, wheels as wh  # noqa: E402

from tests.agents.test_server_provisioning import _make_sources, _vault_mod  # noqa: E402

SHA = "a" * 40


def _extra_tree(cfg):
    """Adds what a release has besides program/agent: migrations, units, helpers, locks."""
    rel = cfg.parent / "release"
    (rel / "satellite/migrations").mkdir(parents=True)
    (rel / "satellite/migrations/0001_layout.sh").write_text("exit 0")
    (rel / "satellite/program/friday-satelite.service").write_text("[Service]")
    (rel / "satellite/program/install.sh").write_text("deploy")
    (rel / "satellite/program/requirements.lock").write_text("numpy==2.3.0\n")
    (rel / "agents/satellite/requirements.lock").write_text("aiohttp==3.13.0\n")
    (rel / "agents/satellite/friday-satellite-agent.service").write_text("[Service]")
    (rel / "agents/satellite/sudoers.friday-satellite-agent").write_text("jose ALL=(root) NOPASSWD: /bin/true")
    (rel / "satellite/asound.conf").write_text("pcm.!default {}")
    (rel / "ota").mkdir()
    (rel / "ota/release.json").write_text(json.dumps({"schema": 1, "protocol": {"satellite": 1, "server": 1, "update": 1},
                                                      "min_protocol": {}, "requires": {}, "heavy": [],
                                                      "components": {"satellite": ["satellite"]}}))
    return rel


@pytest.fixture
def src(tmp_path, monkeypatch):
    cfg = tmp_path / "haos"
    archive, asked = _make_sources(cfg, monkeypatch)
    _extra_tree(cfg)
    return archive, asked, tmp_path


def _members(path):
    with tarfile.open(path, "r:gz") as t:
        return {m.name: (t.extractfile(m).read() if m.isfile() else None) for m in t.getmembers()}


def test_satellite_package_layout(src):
    archive, _, tmp = src
    out = tmp / "pkg"
    info = pk.build_satellite(archive, "v9.9.9", SHA, pv._vault(), str(out))
    files = _members(info["path"])
    assert files["program/friday_satelite.py"] and "program/test_maestro.py" not in files
    assert files["program/vendor/xvf_host.py"] == b"v" and files["program/sons/onboarding/ready.wav"] == b"w"
    assert "program/sons/alarme.wav" not in files
    assert files["program/FRIDAY_VERSION"] == f"v9.9.9 {SHA}\n".encode()
    assert files["agent/friday_agent_core/FRIDAY_VERSION"] == f"v9.9.9 {SHA}\n".encode()
    assert files["migrations/0001_layout.sh"] == b"exit 0" and "system/install.sh" not in files
    assert {"system/friday-satelite.service", "system/friday-satellite-agent.service", "system/asound.conf",
            "system/sudoers.friday-satellite-agent", "program/requirements.lock", "agent/requirements.lock"} <= set(files)
    man = json.loads(files["manifest.json"])
    assert man["tag"] == "v9.9.9" and man["sha"] == SHA and man["schema"] == 1 and man["migrations"] == ["0001_layout.sh"]
    assert man["lock_sha256"] == pk.lock_sha256(b"numpy==2.3.0\n", b"aiohttp==3.13.0\n") == info["lock_sha256"]
    assert info["sha256"] == hashlib.sha256(Path(info["path"]).read_bytes()).hexdigest()
    assert info["size"] == Path(info["path"]).stat().st_size


def test_old_bundle_contract_still_satisfied(src):
    """An agent of v0.2.x validates program/friday_satelite.py + agent/... and ignores the rest (frozen contract)."""
    archive, _, tmp = src
    files = _members(pk.build_satellite(archive, "v9.9.9", SHA, pv._vault(), str(tmp / "pkg"))["path"])
    assert "program/friday_satelite.py" in files and "agent/friday_satellite_agent/__main__.py" in files
    assert any(n.startswith("agent/friday_agent_core/") for n in files)


def test_package_refuses_missing_vault_set(tmp_path, monkeypatch):
    cfg = tmp_path / "haos"
    archive, _ = _make_sources(cfg, monkeypatch)
    _extra_tree(cfg)
    man = cfg.parent / "release" / "vault" / "manifest.json"
    data = json.loads(man.read_text())
    data["files"] = {k: v for k, v in data["files"].items() if "onboarding" not in k}
    man.write_text(json.dumps(data))
    with pytest.raises(pv.ProvisioningError) as e:
        pk.build_satellite(archive, "v9.9.9", SHA, pv._vault(), str(tmp_path / "pkg"))
    assert e.value.code == "vault_error"
    assert not list((tmp_path / "pkg").glob("*.tar.gz"))


class FakeRepo:
    def __init__(self, archive):
        self.calls = []
        self._archive = archive

    def archive(self, sha, paths=None):
        self.calls.append(("archive", sha, tuple(paths or ())))
        return self._archive("v9.9.9", list(paths) if paths else ["satellite", "agents", "vault", "ota"])


def test_build_release_is_idempotent(src):
    archive, _, tmp = src
    repo = FakeRepo(archive)
    built = []
    root = tmp / "ota"
    out = pk.build_release(repo, "v9.9.9", SHA, pv._vault(), str(root), wheels=lambda locks, sha: built.append(sha) or "w")
    assert {"satellite", "server"} <= set(out) and (root / "packages/v9.9.9/.complete").exists()
    assert (root / "packages/v9.9.9/satellite.tar.gz").exists() and (root / "packages/v9.9.9/archive.tar").exists()
    n = len(repo.calls)
    again = pk.build_release(repo, "v9.9.9", SHA, pv._vault(), str(root), wheels=lambda locks, sha: 1 / 0)
    assert len(repo.calls) == n and again["satellite"]["sha256"] == out["satellite"]["sha256"]
    assert built == [out["satellite"]["lock_sha256"]]


def test_prune_packages_keeps_the_set(tmp_path):
    for t in ("v0.1.0", "v0.2.0", "v0.3.0"):
        (tmp_path / "packages" / t).mkdir(parents=True)
    removed = pk.prune_packages(str(tmp_path), keep={"v0.3.0", "v0.1.0"})
    assert removed == ["v0.2.0"] and sorted(os.listdir(tmp_path / "packages")) == ["v0.1.0", "v0.3.0"]


def test_wheels_cached_by_lock_sha(tmp_path):
    calls = []
    lock = tmp_path / "a.lock"
    lock.write_text("x==1\n")

    def run(cmd, **kw):
        calls.append(cmd)
        out = Path(next(c.split(":")[0] for c in cmd if c.endswith(":/w")))
        (out / "x-1-py3-none-any.whl").write_bytes(b"whl")
        return type("R", (), {"returncode": 0, "stdout": b"", "stderr": b""})()
    p1 = wh.build([str(lock)], "f" * 64, out_root=str(tmp_path / "wheels"), run=run)
    p2 = wh.build([str(lock)], "f" * 64, out_root=str(tmp_path / "wheels"), run=run)
    assert p1 == p2 and len(calls) == 1 and "--platform" in calls[0] and "linux/arm64" in calls[0]
    with tarfile.open(p1, "r:gz") as t:
        assert t.getnames() == ["wheels/x-1-py3-none-any.whl"]


def test_wheels_failure_leaves_no_cache(tmp_path):
    lock = tmp_path / "a.lock"
    lock.write_text("x==1\n")
    run = lambda cmd, **kw: type("R", (), {"returncode": 1, "stdout": b"", "stderr": b"boom"})()
    with pytest.raises(wh.WheelsError):
        wh.build([str(lock)], "e" * 64, out_root=str(tmp_path / "wheels"), run=run)
    assert not (tmp_path / "wheels" / ("e" * 64) / "wheels.tar.gz").exists()


def test_vm_repo_archive_keeps_only_existing_paths(tmp_path):
    seen = []

    def run(cmd, **kw):
        seen.append(cmd)
        if "ls-tree" in cmd:
            return type("R", (), {"returncode": 0, "stdout": b"satellite/program\nagents/common\n", "stderr": b""})()
        return type("R", (), {"returncode": 0, "stdout": b"TAR", "stderr": b""})()
    r = rp.VmRepo(root=str(tmp_path / "repo"), url="https://example.invalid/x.git", run=run)
    assert r.archive(SHA, ["satellite/program", "satellite/migrations", "agents/common"]) == b"TAR"
    arch = [c for c in seen if "archive" in c][0]
    assert arch[-2:] == ["satellite/program", "agents/common"] and SHA in arch


def test_vm_repo_manifest_absent_is_none(tmp_path):
    def run(cmd, **kw):
        return type("R", (), {"returncode": 128, "stdout": b"", "stderr": b"fatal: path does not exist"})()
    assert rp.VmRepo(root=str(tmp_path), url="u", run=run).manifest(SHA) is None


def test_bundle_update_serves_the_built_package(src, tmp_path, monkeypatch):
    archive, asked, tmp = src
    info = pk.build_satellite(archive, "v9.9.9", SHA, pv._vault(), str(tmp / "pkg"))
    n = len(asked)
    store = pv.BundleStore(archive=archive, package_for_update=lambda: info["path"])
    token = store.prepare("update", "sat-abc123")["url"].rsplit("/", 1)[1]
    assert store.take(token) == Path(info["path"]).read_bytes() and len(asked) == n
