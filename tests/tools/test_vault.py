"""Vault: sha256-addressed private store for files that cannot be public (plan 2026-11-09 Task 4)."""
import hashlib
import importlib.util
import json
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("vault", ROOT / "scripts" / "vault.py")
vault = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(vault)


def _setup(tmp_path):
    root, man = tmp_path / "vault", tmp_path / "manifest.json"
    src = tmp_path / "src"; src.mkdir()
    return root, man, src


def test_put_then_fetch_roundtrip(tmp_path):
    root, man, src = _setup(tmp_path)
    (src / "a.wav").write_bytes(b"RIFFdata")
    sha = vault.put(str(src / "a.wav"), "satellite/sounds/a.wav", root=str(root), manifest=str(man))
    assert sha == hashlib.sha256(b"RIFFdata").hexdigest()
    vault.fetch("satellite/sounds/a.wav", str(tmp_path / "out.wav"), root=str(root), manifest=str(man))
    assert (tmp_path / "out.wav").read_bytes() == b"RIFFdata"


def test_fetch_wrong_hash_raises_and_leaves_no_file(tmp_path):
    root, man, src = _setup(tmp_path)
    (src / "a").write_bytes(b"x")
    sha = vault.put(str(src / "a"), "n/a", root=str(root), manifest=str(man))
    (root / "sha256" / sha).write_bytes(b"tampered")
    with pytest.raises(vault.VaultError):
        vault.fetch("n/a", str(tmp_path / "out"), root=str(root), manifest=str(man))
    assert not (tmp_path / "out").exists()


def test_fetch_all_is_all_or_nothing(tmp_path):
    root, man, src = _setup(tmp_path)
    (src / "a").write_bytes(b"a")
    vault.put(str(src / "a"), "x/a", root=str(root), manifest=str(man))
    m = json.loads(man.read_text())
    m["files"]["x/b"] = {"sha256": "0" * 64, "size": 1, "source": "b"}
    man.write_text(json.dumps(m))
    out = tmp_path / "out"
    with pytest.raises(vault.VaultError):
        vault.fetch_all(["x/a", "x/b"], str(out), root=str(root), manifest=str(man))
    assert not (out / "x" / "a").exists()


def test_manifest_has_no_absolute_house_paths(tmp_path):
    root, man, src = _setup(tmp_path)
    (src / "a").write_bytes(b"a")
    vault.put(str(src / "a"), "x/a", root=str(root), manifest=str(man), source="tools/satelite/sons/a")
    entry = json.loads(man.read_text())["files"]["x/a"]
    assert not entry["source"].startswith("/")


def test_verify_reports_bad_entries(tmp_path):
    root, man, src = _setup(tmp_path)
    (src / "a").write_bytes(b"a")
    sha = vault.put(str(src / "a"), "x/a", root=str(root), manifest=str(man))
    assert vault.verify(root=str(root), manifest=str(man)) == []
    (root / "sha256" / sha).unlink()
    assert vault.verify(root=str(root), manifest=str(man)) == ["x/a"]


def test_cli_refuses_when_vault_root_is_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("FRIDAY_VAULT", str(tmp_path / "nope"))
    (tmp_path / "a").write_bytes(b"a")
    assert vault.main(["put", str(tmp_path / "a"), "x/a"]) == 2
    assert not (tmp_path / "nope").exists()
