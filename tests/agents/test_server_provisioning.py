"""Server agent provisioning: sync-key registration and one-time bundles (temporary directories only)."""

import hashlib
import io
import os
import sys
import tarfile
from pathlib import Path

import pytest
from aiohttp import web

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "agents" / "server"))
from friday_server_agent import provisioning as pv  # noqa: E402

KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIPlaceholderPlaceholderPlaceholder123"
KEY2 = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOtherOtherOtherOtherOtherOtherOth4567"


@pytest.fixture
def dirs(tmp_path, monkeypatch):
    data, cfg, bundles = tmp_path / "data", tmp_path / "haos", tmp_path / "bundles"
    data.mkdir()
    monkeypatch.setenv("FRIDAY_WAKEWORD_DATA", str(data))
    monkeypatch.setenv("FRIDAY_HAOS_CONFIG", str(cfg))
    monkeypatch.setenv("FRIDAY_BUNDLES_DIR", str(bundles))
    monkeypatch.setenv("FRIDAY_BUNDLE_HOST", "vm.test:8801")
    return data, cfg, bundles


def _lines(data):
    return (data / ".ssh" / "authorized_keys").read_text().splitlines()


def test_register_new_key_writes_rrsync_line_and_folder(dirs):
    data, *_ = dirs
    r = pv.register_sync_key("sat-abc123", KEY + " sat-abc123-satsync", chown_user=None)
    assert r == {"ok": True, "changed": True}
    assert (data / "incoming" / "sat-abc123").is_dir()
    assert _lines(data) == [
        f'restrict,command="/usr/bin/rrsync -wo -no-del {data}/incoming/sat-abc123" {KEY} sat-abc123-satsync']
    assert (data / ".ssh" / "authorized_keys").stat().st_mode & 0o777 == 0o600


def test_register_is_idempotent(dirs):
    data, *_ = dirs
    pv.register_sync_key("sat-abc123", KEY, chown_user=None)
    assert pv.register_sync_key("sat-abc123", KEY + " comment", chown_user=None)["changed"] is False
    assert len(_lines(data)) == 1


def test_register_changed_key_replaces_only_that_line(dirs):
    data, *_ = dirs
    ssh = data / ".ssh"
    ssh.mkdir()
    (ssh / "authorized_keys").write_text("ssh-rsa OTHERKEY someone\n")
    pv.register_sync_key("sat-abc123", KEY, chown_user=None)
    pv.register_sync_key("sat-def456", KEY2, chown_user=None)
    pv.register_sync_key("sat-abc123", KEY2, chown_user=None)
    lines = _lines(data)
    assert lines[0] == "ssh-rsa OTHERKEY someone"
    assert len(lines) == 3
    assert sum(1 for ln in lines if ln.endswith(" sat-abc123-satsync")) == 1
    assert [ln for ln in lines if ln.endswith("sat-abc123-satsync")][0].count(KEY2) == 1


@pytest.mark.parametrize("sat_id", ["sat-ABC123", "sat-12345", "../x", "sat-abc12g", "", None, 3])
def test_register_invalid_sat_id(dirs, sat_id):
    with pytest.raises(pv.ProvisioningError) as e:
        pv.register_sync_key(sat_id, KEY, chown_user=None)
    assert e.value.code == "invalid_sat_id"


@pytest.mark.parametrize("key", ["ssh-rsa AAAAB3Nza", "ssh-ed25519 short", KEY + '" evil', "", None,
                                 'ssh-ed25519 ' + 'A' * 50 + '"\nrestrict x'])
def test_register_invalid_key(dirs, key):
    data, *_ = dirs
    with pytest.raises(pv.ProvisioningError) as e:
        pv.register_sync_key("sat-abc123", key, chown_user=None)
    assert e.value.code == "invalid_key"
    assert not (data / ".ssh" / "authorized_keys").exists()


VAULT_PY = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "scripts", "vault.py")


def _vault_mod():
    import importlib.util
    spec = importlib.util.spec_from_file_location("vault_for_tests", VAULT_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _make_sources(cfg, monkeypatch=None, tamper=False):
    """A release tree (what `git archive` of the ref gives) plus a vault with the private files."""
    rel = cfg.parent / "release"
    prog = rel / "satellite/program"
    (prog / "vendor").mkdir(parents=True)
    (prog / "__pycache__").mkdir()
    for n in ("friday_satelite.py", "maestro.py", "test_maestro.py", "notes.md"):
        (prog / n).write_text(n)
    (prog / "vendor/SOURCE.md").write_text("s")
    (prog / "__pycache__/x.pyc").write_bytes(b"c")
    for d, files in (("agents/satellite/friday_satellite_agent", ["__main__.py", "test_x.py"]),
                     ("agents/common/friday_agent_core", ["__init__.py"])):
        (rel / d).mkdir(parents=True)
        for f in files:
            (rel / d / f).write_text(f)
    vault = _vault_mod()
    root, man, src = cfg.parent / "vault", cfg.parent / "manifest.json", cfg.parent / "vsrc"
    src.mkdir()
    (src / "xvf_host.py").write_text("v")
    (src / "ready.wav").write_bytes(b"w")
    (src / "alarme.wav").write_bytes(b"a")
    vault.put(str(src / "xvf_host.py"), "satellite/program/vendor/xvf_host.py", root=str(root), manifest=str(man))
    sha = vault.put(str(src / "ready.wav"), "satellite/sounds/onboarding/ready.wav", root=str(root), manifest=str(man))
    vault.put(str(src / "alarme.wav"), "satellite/sounds/alarme.wav", root=str(root), manifest=str(man))
    if tamper:
        (root / "sha256" / sha).write_bytes(b"tampered")
    if monkeypatch:
        monkeypatch.setenv("FRIDAY_VAULT", str(root))
        monkeypatch.setenv("FRIDAY_VAULT_MANIFEST", str(man))
        monkeypatch.setenv("FRIDAY_VAULT_PY", VAULT_PY)
        monkeypatch.setenv("FRIDAY_RELEASE_REF", "v9.9.9")
    asked = []

    def archive(ref, paths):
        asked.append((ref, tuple(paths)))
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w") as tar:
            for p in paths:
                tar.add(str(rel / p), arcname=p)
        return buf.getvalue()
    if monkeypatch:
        monkeypatch.setattr(pv, "git_archive", archive)
    return archive, asked


def _names(body):
    with tarfile.open(fileobj=io.BytesIO(body), mode="r:gz") as t:
        return sorted(t.getnames())


def test_update_bundle_layout_and_filters(dirs, monkeypatch):
    _, cfg, bundles = dirs
    archive, asked = _make_sources(cfg, monkeypatch)
    store = pv.BundleStore(archive=archive)
    r = store.prepare("update", "sat-abc123")
    assert r["url"].startswith("http://vm.test:8801/v1/bundles/") and r["expires_in"] == 600
    token = r["url"].rsplit("/", 1)[1]
    body = store.take(token)
    assert hashlib.sha256(body).hexdigest() == r["sha256"]
    assert _names(body) == [
        "agent/friday_agent_core/FRIDAY_VERSION", "agent/friday_agent_core/__init__.py",
        "agent/friday_satellite_agent/__main__.py", "program/FRIDAY_VERSION", "program/friday_satelite.py", "program/maestro.py",
        "program/sons/onboarding/ready.wav", "program/vendor/SOURCE.md", "program/vendor/xvf_host.py"]
    assert asked and asked[0][0] == "v9.9.9"


def test_update_bundle_files_are_readable_whatever_the_source_mode(dirs, monkeypatch):
    """06/11: through the HAOS mount the sources look like 0600 and the satellite agent could not read itself."""
    _, cfg, _ = dirs
    archive, _ = _make_sources(cfg, monkeypatch)
    for root, _d, files in os.walk(cfg.parent / "release"):
        for f in files:
            os.chmod(os.path.join(root, f), 0o600)
    store = pv.BundleStore(archive=archive)
    token = store.prepare("update", "sat-abc123")["url"].rsplit("/", 1)[1]
    with tarfile.open(fileobj=io.BytesIO(store.take(token))) as tar:
        members = tar.getmembers()
    assert members and all(m.mode == 0o644 and m.uid == 0 for m in members)


def test_token_is_single_use_and_file_removed(dirs, monkeypatch):
    _, cfg, bundles = dirs
    _make_sources(cfg, monkeypatch)
    store = pv.BundleStore()
    token = store.prepare("update", "sat-abc123")["url"].rsplit("/", 1)[1]
    assert store.take(token) is not None
    assert store.take(token) is None
    assert list(bundles.iterdir()) == []


def test_token_expires_after_ten_minutes(dirs, monkeypatch):
    _, cfg, bundles = dirs
    _make_sources(cfg, monkeypatch)
    now = [1000.0]
    store = pv.BundleStore(clock=lambda: now[0])
    token = store.prepare("update", "sat-abc123")["url"].rsplit("/", 1)[1]
    now[0] += 599
    t2 = store.prepare("update", "sat-abc123")["url"].rsplit("/", 1)[1]
    now[0] += 2  # the first is now 601 s old
    assert store.take(token) is None
    assert store.take(t2) is not None
    assert list(bundles.iterdir()) == []
    assert store.take("never-issued") is None


def test_voices_bundle(dirs):
    data, *_ = dirs
    v = data / "voices"
    (v / "ana/jabra-speak-410").mkdir(parents=True)
    (v / "bia/xvf3800").mkdir(parents=True)
    (v / "ana/jabra-speak-410/a.wav").write_bytes(b"a")
    (v / "bia/xvf3800/b.wav").write_bytes(b"b")
    old, new = v / "friday_verificador.pkl", v / "bia" / "friday_verificador.pkl"
    old.write_bytes(b"OLD")
    new.write_bytes(b"NEW")
    import os
    os.utime(old, (1, 1))
    store = pv.BundleStore()
    body = store.take(store.prepare("voices", "sat-abc123")["url"].rsplit("/", 1)[1])
    assert _names(body) == ["friday_verificador.pkl", "vozes/ana/jabra-speak-410/a.wav", "vozes/bia/xvf3800/b.wav"]
    with tarfile.open(fileobj=io.BytesIO(body), mode="r:gz") as t:
        assert t.extractfile("friday_verificador.pkl").read() == b"NEW"


def test_voices_calls_consolidate_first(dirs):
    data, *_ = dirs
    calls = []

    def consolidate(d):
        calls.append(d)
        (data / "voices/ana").mkdir(parents=True)
        (data / "voices/ana/a.wav").write_bytes(b"a")

    store = pv.BundleStore(consolidate=consolidate)
    store.prepare("voices", "sat-abc123")
    assert calls == [str(data)]


def test_prepare_errors(dirs):
    store = pv.BundleStore()
    for kind, sat, code in (("firmware", "sat-abc123", "invalid_kind"), ("update", "bad", "invalid_sat_id"),
                            ("update", "sat-abc123", "source_missing"), ("voices", "sat-abc123", "source_missing")):
        with pytest.raises(pv.ProvisioningError) as e:
            store.prepare(kind, sat)
        assert e.value.code == code
    _, _, bundles = dirs
    assert list(bundles.iterdir()) == []  # no half-built file left behind


async def test_http_route_serves_once(dirs, aiohttp_client, monkeypatch):
    _, cfg, _ = dirs
    _make_sources(cfg, monkeypatch)
    store = pv.BundleStore()
    app = web.Application()
    pv.add_routes(app, store)
    client = await aiohttp_client(app)
    token = store.prepare("update", "sat-abc123")["url"].rsplit("/", 1)[1]
    r = await client.get(f"/v1/bundles/{token}")
    assert r.status == 200 and len(await r.read()) > 100
    assert (await client.get(f"/v1/bundles/{token}")).status == 404
    assert (await client.get("/v1/bundles/unknown")).status == 404


async def test_handlers(dirs, monkeypatch):
    data, cfg, _ = dirs
    _make_sources(cfg, monkeypatch)
    h = pv.make_handlers(pv.BundleStore())
    r = await h["register_sync_key"]({"sat_id": "sat-abc123", "pubkey": KEY})
    assert r["ok"]
    r = await h["register_sync_key"]({"args": {"sat_id": "nope", "pubkey": KEY}})
    assert r == {"ok": False, "error": "invalid_sat_id"}
    r = await h["prepare_bundle"]({"kind": "update", "sat_id": "sat-abc123"})
    assert r["ok"] and set(r["data"]) == {"url", "sha256", "expires_in"}
    assert (await h["prepare_bundle"]({"kind": "x", "sat_id": "sat-abc123"})) == {"ok": False, "error": "invalid_kind"}


def test_update_bundle_comes_from_git_ref(dirs, monkeypatch):
    _, cfg, _ = dirs
    archive, asked = _make_sources(cfg, monkeypatch)
    monkeypatch.setenv("FRIDAY_RELEASE_REF", "abc1234")
    store = pv.BundleStore(archive=archive)
    body = store.take(store.prepare("update", "sat-abc123")["url"].rsplit("/", 1)[1])
    assert asked[0][0] == "abc1234"
    with tarfile.open(fileobj=io.BytesIO(body)) as tar:
        assert tar.extractfile("program/FRIDAY_VERSION").read().decode().split()[0] == "abc1234"


def test_update_bundle_ref_defaults_to_the_installed_server_version(dirs, monkeypatch, tmp_path):
    _, cfg, _ = dirs
    archive, asked = _make_sources(cfg, monkeypatch)
    monkeypatch.delenv("FRIDAY_RELEASE_REF")
    vf = tmp_path / "FRIDAY_VERSION"
    vf.write_text("v0.2.0 0123456789abcdef0123456789abcdef01234567\n")
    monkeypatch.setenv("FRIDAY_AGENT_VERSION_FILE", str(vf))
    store = pv.BundleStore(archive=archive)
    store.prepare("update", "sat-abc123")
    assert asked[0][0] == "0123456789abcdef0123456789abcdef01234567"


def test_update_bundle_aborts_on_vault_hash_mismatch(dirs, monkeypatch):
    _, cfg, bundles = dirs
    archive, _ = _make_sources(cfg, monkeypatch, tamper=True)
    store = pv.BundleStore(archive=archive)
    with pytest.raises(pv.ProvisioningError) as e:
        store.prepare("update", "sat-abc123")
    assert e.value.code == "vault_error"
    assert not list(bundles.glob("*.tar.gz")) if bundles.exists() else True


def test_update_bundle_never_reads_working_folder(dirs, monkeypatch):
    _, cfg, _ = dirs
    archive, _ = _make_sources(cfg, monkeypatch)
    old = cfg / "tools/satelite/programa"
    old.mkdir(parents=True)
    (old / "only_in_working_folder.py").write_text("x")
    store = pv.BundleStore(archive=archive)
    body = store.take(store.prepare("update", "sat-abc123")["url"].rsplit("/", 1)[1])
    assert "program/only_in_working_folder.py" not in _names(body)


def test_bundle_carries_the_version_for_program_and_agent(dirs, monkeypatch):
    _, cfg, _ = dirs
    archive, _ = _make_sources(cfg, monkeypatch)
    store = pv.BundleStore(archive=archive)
    body = store.take(store.prepare("update", "sat-abc123")["url"].rsplit("/", 1)[1])
    with tarfile.open(fileobj=io.BytesIO(body)) as tar:
        for arc in ("program/FRIDAY_VERSION", "agent/friday_agent_core/FRIDAY_VERSION"):
            assert tar.extractfile(arc).read().decode().split()[0] == "v9.9.9"
