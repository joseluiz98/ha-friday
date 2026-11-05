"""consolidate(): voices gathered by person and microphone from the satellites' nightly uploads."""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "agents" / "server"))
from friday_server_agent import provisioning as pv  # noqa: E402
from friday_server_agent import voices  # noqa: E402


def put(path: Path, data: bytes = b"x", mtime=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    if mtime:
        os.utime(path, (mtime, mtime))


def tree(root: Path):
    return sorted(str(p.relative_to(root)) for p in root.rglob("*") if p.is_file())


def test_two_pis_two_microphones(tmp_path):
    inc = tmp_path / "incoming"
    put(inc / "sat-aaaaaa/mic", b"jabra-speak-410\n")
    put(inc / "sat-aaaaaa/vozes/ana/atual/t01.wav", b"a1")
    put(inc / "sat-aaaaaa/vozes/ana/atual/t02.wav", b"a2")
    put(inc / "sat-aaaaaa/vozes/ana/cadastro.json", b"{}")
    put(inc / "sat-bbbbbb/mic", b"xvf3800\n")
    put(inc / "sat-bbbbbb/vozes/ana/atual/t01.wav", b"b1")
    put(inc / "sat-bbbbbb/vozes/bia/atual/t01.wav", b"c1")
    r = voices.consolidate(str(tmp_path))
    assert tree(tmp_path / "voices") == [
        "ana/jabra-speak-410/atual/t01.wav", "ana/jabra-speak-410/atual/t02.wav",
        "ana/xvf3800/atual/t01.wav", "bia/xvf3800/atual/t01.wav"]
    assert (tmp_path / "voices/ana/xvf3800/atual/t01.wav").read_bytes() == b"b1"  # same name, other mic: kept apart
    assert r["copied"] == 4 and r["people"] == ["ana", "bia"]


def test_repeat_does_not_duplicate_or_recopy(tmp_path):
    put(tmp_path / "incoming/sat-aaaaaa/vozes/ana/atual/t01.wav", b"a1")
    assert voices.consolidate(str(tmp_path))["copied"] == 1
    assert voices.consolidate(str(tmp_path))["copied"] == 0
    assert tree(tmp_path / "voices") == ["ana/jabra-speak-410/atual/t01.wav"]  # no mic file: the Jabra default


def test_a_changed_file_is_updated(tmp_path):
    src = tmp_path / "incoming/sat-aaaaaa/vozes/ana/atual/t01.wav"
    put(src, b"v1", mtime=1000)
    voices.consolidate(str(tmp_path))
    put(src, b"v2-longer", mtime=2000)
    assert voices.consolidate(str(tmp_path))["copied"] == 1
    assert (tmp_path / "voices/ana/jabra-speak-410/atual/t01.wav").read_bytes() == b"v2-longer"


def test_unsafe_names_are_ignored(tmp_path):
    inc = tmp_path / "incoming/sat-aaaaaa"
    put(inc / "mic", b"../../etc\n")
    put(inc / "vozes/ana/atual/t01.wav", b"a")
    put(inc / "vozes/Bad Name/t.wav", b"b")
    put(inc / "vozes/.hidden/t.wav", b"c")
    put(inc / "vozes/ana/notes.txt", b"d")
    voices.consolidate(str(tmp_path))
    assert tree(tmp_path / "voices") == ["ana/jabra-speak-410/atual/t01.wav"]


def test_newest_verifier_is_gathered(tmp_path):
    put(tmp_path / "incoming/sat-aaaaaa/friday_verificador.pkl", b"OLD", mtime=1000)
    put(tmp_path / "incoming/sat-bbbbbb/friday_verificador.pkl", b"NEW", mtime=2000)
    voices.consolidate(str(tmp_path))
    assert (tmp_path / "voices/friday_verificador.pkl").read_bytes() == b"NEW"


def test_nothing_incoming_is_fine(tmp_path):
    assert voices.consolidate(str(tmp_path)) == {"copied": 0, "people": []}


def test_bundle_lists_the_people(tmp_path, monkeypatch):
    monkeypatch.setenv("FRIDAY_WAKEWORD_DATA", str(tmp_path))
    monkeypatch.setenv("FRIDAY_BUNDLES_DIR", str(tmp_path / "bundles"))
    put(tmp_path / "incoming/sat-aaaaaa/vozes/ana/atual/t01.wav", b"a")
    put(tmp_path / "incoming/sat-aaaaaa/vozes/bia/atual/t01.wav", b"b")
    store = pv.BundleStore(consolidate=voices.consolidate)
    r = store.prepare("voices", "sat-abc123")
    assert r["people"] == ["ana", "bia"]
