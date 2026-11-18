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
    (rel / "agents/satellite/friday-ota-apply").write_text("#!/usr/bin/env python3")
    (rel / "satellite/audio").mkdir(parents=True)
    (rel / "satellite/audio/jabra.conf").write_text("pcm.!default {}")
    (rel / "satellite/audio/xvf3800.conf").write_text("pcm.!default {}")
    (rel / "ota").mkdir()
    (rel / "ota/release.json").write_text(json.dumps(_release_json()))
    return rel


DECLARED_SYSTEM = ["satellite/program", "satellite/audio/jabra.conf", "satellite/audio/xvf3800.conf",
                   "agents/satellite/friday-satellite-agent.service", "agents/satellite/sudoers.friday-satellite-agent",
                   "agents/satellite/friday-ota-apply"]


def _release_json(contract=True, system=None):
    data = {"schema": 1, "protocol": {"satellite": 1, "server": 1, "update": 1}, "min_protocol": {}, "requires": {},
            "heavy": [], "components": {"satellite": ["satellite"]}}
    if contract:
        data["package"] = {"satellite": {"paths": ["satellite/program", "satellite/migrations", "satellite/audio",
                                                   "agents/satellite", "agents/common/friday_agent_core"],
                                         "system": DECLARED_SYSTEM if system is None else system}}
    return data


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
    assert {"system/friday-satelite.service", "system/friday-satellite-agent.service", "system/jabra.conf", "system/xvf3800.conf",
            "system/sudoers.friday-satellite-agent", "system/friday-ota-apply", "program/requirements.lock",
            "agent/requirements.lock"} <= set(files)
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
        assert t.getnames() == ["x-1-py3-none-any.whl"]  # flat: see tests/contract/test_wheels_layout.py


def test_wheels_failure_leaves_no_cache(tmp_path):
    lock = tmp_path / "a.lock"
    lock.write_text("x==1\n")
    run = lambda cmd, **kw: type("R", (), {"returncode": 1, "stdout": b"", "stderr": b"boom"})()
    with pytest.raises(wh.WheelsError):
        wh.build([str(lock)], "e" * 64, out_root=str(tmp_path / "wheels"), run=run)
    assert not list((tmp_path / "wheels" / ("e" * 64)).glob("wheels*.tar.gz"))


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
        if "show" in cmd:
            return type("R", (), {"returncode": 128, "stdout": b"", "stderr": b"fatal: path does not exist"})()
        return type("R", (), {"returncode": 0, "stdout": b"", "stderr": b""})()  # ls-tree: really absent
    assert rp.VmRepo(root=str(tmp_path), url="u", run=run).manifest(SHA) is None


def test_bundle_update_serves_the_built_package(src, tmp_path, monkeypatch):
    monkeypatch.setenv("FRIDAY_BUNDLES_DIR", str(tmp_path / "bundles"))
    archive, asked, tmp = src
    info = pk.build_satellite(archive, "v9.9.9", SHA, pv._vault(), str(tmp / "pkg"))
    n = len(asked)
    store = pv.BundleStore(archive=archive, package_for_update=lambda: info["path"])
    token = store.prepare("update", "sat-abc123")["url"].rsplit("/", 1)[1]
    assert store.take(token) == Path(info["path"]).read_bytes() and len(asked) == n


def test_modern_release_requires_locks_and_units(src, tmp_path):
    archive, _, tmp = src
    (tmp / "release" / "agents/satellite/requirements.lock").unlink()
    with pytest.raises(pv.ProvisioningError) as e:
        pk.build_satellite(archive, "v9.9.9", SHA, pv._vault(), str(tmp / "pkg2"))
    assert e.value.code == "source_missing"


def test_manifest_carries_heavy_flag(src):
    archive, _, tmp = src
    rel = tmp / "release" / "ota/release.json"
    data = json.loads(rel.read_text())
    data["heavy"] = ["satellite"]
    rel.write_text(json.dumps(data))
    files = _members(pk.build_satellite(archive, "v9.9.9", SHA, pv._vault(), str(tmp / "pkg3"))["path"])
    assert json.loads(files["manifest.json"])["heavy"] is True


def test_manifest_git_error_is_not_an_old_release(tmp_path):
    def run(cmd, **kw):
        if "show" in cmd:
            return type("R", (), {"returncode": 128, "stdout": b"", "stderr": b"fatal: unable to fetch"})()
        return type("R", (), {"returncode": 0, "stdout": b"ota/release.json\n", "stderr": b""})()
    with pytest.raises(rp.RepoError):
        rp.VmRepo(root=str(tmp_path), url="u", run=run).manifest(SHA)


def test_fallback_bundle_writes_version_line_verbatim(tmp_path, monkeypatch):
    cfg = tmp_path / "haos"
    archive, _ = _make_sources(cfg, monkeypatch)  # FRIDAY_RELEASE_REF=v9.9.9 (a bare ref)
    monkeypatch.setenv("FRIDAY_BUNDLES_DIR", str(tmp_path / "bundles"))
    store = pv.BundleStore(archive=archive)
    token = store.prepare("update", "sat-abc123")["url"].rsplit("/", 1)[1]
    with tarfile.open(fileobj=io.BytesIO(store.take(token))) as t:
        assert t.extractfile("program/FRIDAY_VERSION").read() == b"v9.9.9\n"


def test_wheels_use_no_deps(tmp_path):
    calls = []
    lock = tmp_path / "a.lock"
    lock.write_text("x==1\n")

    def run(cmd, **kw):
        calls.append(cmd)
        out = Path(next(c.split(":")[0] for c in cmd if c.endswith(":/w")))
        (out / "x.whl").write_bytes(b"w")
        return type("R", (), {"returncode": 0, "stdout": b"", "stderr": b""})()
    wh.build([str(lock)], "d" * 64, out_root=str(tmp_path / "w"), run=run)
    assert "--no-deps" in calls[0]


def test_a_bundle_of_the_old_layout_is_never_reused(tmp_path):
    """14/11: the cache is keyed by the lock; a bundle built with the old layout (wheels/ prefix) must be rebuilt."""
    old = tmp_path / ("f" * 64) / "wheels.tar.gz"
    old.parent.mkdir(parents=True)
    old.write_bytes(b"old layout")
    calls = []

    def run(cmd, **kw):
        calls.append(cmd)
        Path(cmd[cmd.index("-v") + 1].split(":", 1)[0], "x-1-py3-none-any.whl").write_bytes(b"w")
        return type("R", (), {"returncode": 0})()

    lock = tmp_path / "a.lock"
    lock.write_text("x==1\n")
    path = wh.build([str(lock)], "f" * 64, out_root=str(tmp_path), run=run)
    assert calls and path != str(old) and Path(path).read_bytes() != b"old layout"


def test_declared_system_files_are_packed(src):
    archive, _, tmp = src
    files = _members(pk.build_satellite(archive, "v9.9.9", SHA, pv._vault(), str(tmp / "pkg"))["path"])
    assert files["system/jabra.conf"] == b"pcm.!default {}" and files["system/xvf3800.conf"] == b"pcm.!default {}"


def test_old_builtin_lists_still_pack_the_declared_audio(src, monkeypatch):
    """The v0.3.8 packager case: its built-in lists know nothing about audio, the release declares it."""
    archive, _, tmp = src
    monkeypatch.setattr(pk, "SATELLITE_PATHS", [p for p in pk.SATELLITE_PATHS if p != "satellite/audio"])
    monkeypatch.setattr(pk, "SYSTEM_SOURCES", [p for p in pk.SYSTEM_SOURCES if not p.startswith("satellite/audio")])
    files = _members(pk.build_satellite(archive, "v9.9.9", SHA, pv._vault(), str(tmp / "pkg"))["path"])
    assert "system/jabra.conf" in files and "system/xvf3800.conf" in files


def test_declared_file_missing_fails_and_writes_nothing(src):
    archive, _, tmp = src
    rel = tmp / "release" / "ota/release.json"
    rel.write_text(json.dumps(_release_json(system=DECLARED_SYSTEM + ["satellite/audio/missing.conf"])))
    with pytest.raises(pv.ProvisioningError) as e:
        pk.build_satellite(archive, "v9.9.9", SHA, pv._vault(), str(tmp / "pkg"))
    assert e.value.code == "source_missing"
    assert not list((tmp / "pkg").glob("*"))


def test_release_without_contract_uses_builtin_lists(src):
    archive, _, tmp = src
    (tmp / "release" / "ota/release.json").write_text(json.dumps(_release_json(contract=False)))
    files = _members(pk.build_satellite(archive, "v9.9.9", SHA, pv._vault(), str(tmp / "pkg"))["path"])
    assert {"system/friday-satelite.service", "system/jabra.conf", "system/xvf3800.conf",
            "system/friday-ota-apply"} <= set(files)


def test_declared_merges_required_paths_without_repeats():
    paths, system = pk.declared({"package": {"satellite": {"paths": ["satellite/program", "x"], "system": ["a", "a"]}}})
    assert paths[:2] == ["satellite/program", "x"] and system == ["a"]
    assert "ota/release.json" in paths and "vault/manifest.json" in paths and "agents/satellite/friday-ota-apply" in paths
    assert "satellite/program/requirements.lock" not in paths  # already inside the declared folder
    assert len(paths) == len(set(paths))
    assert pk.declared({}) [1] == pk.SYSTEM_SOURCES


def _repo_release():
    return json.loads((Path(__file__).resolve().parents[2] / "ota/release.json").read_text())


def test_builtin_lists_equal_the_repo_contract():
    sat = _repo_release()["package"]["satellite"]
    assert (sat["paths"], sat["system"]) == (pk.SATELLITE_PATHS, pk.SYSTEM_SOURCES)


def test_apply_system_names_are_declared():
    """Every plain file name the satellite's apply installs from system/ is covered by the release contract."""
    import ast
    repo = Path(__file__).resolve().parents[2]
    tree = ast.parse((repo / "agents/satellite/friday-ota-apply").read_text())
    system = next(n.value for n in tree.body if isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") == "SYSTEM")
    names = [k.value for k in system.keys if isinstance(k, ast.Constant) and not k.value.startswith(".")]
    declared = [os.path.basename(p) for p in _repo_release()["package"]["satellite"]["system"]]
    covered = set(declared)
    for entry in _repo_release()["package"]["satellite"]["system"]:  # a declared folder covers the files inside it
        if (repo / entry).is_dir():
            covered |= {f.name for f in (repo / entry).iterdir() if f.is_file()}
    existing = {f.name for base in ("satellite", "agents/satellite") for f in (repo / base).rglob("*") if f.is_file()}
    assert names
    for n in names:
        if n in existing:
            assert n in covered, f"{n} is installed by friday-ota-apply but no package.satellite.system entry covers it"


def test_build_release_records_built_by(src):
    archive, _, tmp = src
    root = tmp / "ota"
    out = pk.build_release(FakeRepo(archive), "v9.9.9", SHA, pv._vault(), str(root), wheels=lambda locks, sha: "w",
                           built_by="v9.9.8")
    assert out["built_by"] == "v9.9.8"
    assert json.loads((root / "packages/v9.9.9/meta.json").read_text())["built_by"] == "v9.9.8"


def test_mark_offered_roundtrip(tmp_path):
    assert pk.is_offered(str(tmp_path), "v0.4.0") is False
    (tmp_path / "packages" / "v0.4.0").mkdir(parents=True)
    pk.mark_offered(str(tmp_path), "v0.4.0")
    assert (tmp_path / "packages" / "v0.4.0" / ".offered").exists()
    assert pk.is_offered(str(tmp_path), "v0.4.0") is True
    assert pk.is_offered(str(tmp_path), "v0.3.0") is False


def test_mark_offered_records_first_offer_only(tmp_path):
    pk.mark_offered(str(tmp_path), "v0.4.0", now=100.0)
    pk.mark_offered(str(tmp_path), "v0.4.0", now=200.0)
    data = json.loads((tmp_path / "packages/v0.4.0/.offered").read_text())
    assert data == {"tag": "v0.4.0", "at": 100.0}


def test_satellite_only_rebuild_keeps_server_archive(src):
    archive, _, tmp = src
    root = tmp / "ota"
    repo = FakeRepo(archive)
    pk.build_release(repo, "v9.9.9", SHA, pv._vault(), str(root), wheels=lambda locks, sha: "w", built_by="v9.9.8")
    server = root / "packages/v9.9.9/archive.tar"
    before = server.read_bytes()
    repo.calls.clear()
    out = pk.build_release(repo, "v9.9.9", SHA, pv._vault(), str(root), wheels=lambda locks, sha: "w2",
                           built_by="v9.9.9", satellite_only=True)
    assert server.exists() and server.read_bytes() == before
    assert not any(c[2] == tuple(pk.SERVER_PATHS) for c in repo.calls)
    assert out["built_by"] == "v9.9.9" and out["satellite"]["wheels"] == "w2" and out["server"]["path"] == str(server)
    assert json.loads((root / "packages/v9.9.9/meta.json").read_text())["built_by"] == "v9.9.9"


def test_build_satellite_without_release_json_uses_builtin_lists(src):
    archive, _, tmp = src

    def no_release(sha, paths):
        if list(paths) == ["ota/release.json"]:
            raise rp.RepoError("nothing to archive")
        return archive(sha, paths)

    info = pk.build_satellite(no_release, "v9.9.9", SHA, pv._vault(), str(tmp / "pkg"))
    assert "program/friday_satelite.py" in _members(info["path"])
