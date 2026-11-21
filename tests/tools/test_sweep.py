"""Sweep v2: private list + generic patterns + path-scoped allow rules + history mode (plan 2026-11-09 Task 3)."""
import importlib.util
import re
import pathlib
import shutil
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("sweep", ROOT / "scripts" / "sweep.py")
sweep = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sweep)


def test_private_list_value_fails(tmp_path):
    (tmp_path / "a.py").write_text('OWNER = "Zelda"\n')
    assert sweep.sweep([str(tmp_path)], ["Zelda"], [])


def test_allow_rule_suppresses_translation_token_text(tmp_path):
    d = tmp_path / "translations"; d.mkdir()
    (d / "en.json").write_text('{"token": "Leave blank to keep the stored token."}\n')
    allow = sweep.parse_allow(['*/translations/*.json:"token"'])
    assert sweep.sweep([str(tmp_path)], [], allow) == []


def test_allow_rule_is_path_scoped(tmp_path):
    (tmp_path / "other.json").write_text('{"token": "Leave blank to keep the stored token."}\n')
    allow = sweep.parse_allow(['*/translations/*.json:"token"'])
    assert sweep.sweep([str(tmp_path)], [], allow)


def test_home_jose_allowed_other_home_fails(tmp_path):
    (tmp_path / "a.sh").write_text("cd /home/jose/satelite\n")
    assert sweep.sweep([str(tmp_path)], [], []) == []
    (tmp_path / "b.sh").write_text("cd /home/zelda/x\n")
    assert [f.path for f in sweep.sweep([str(tmp_path)], [], [])] == [str(tmp_path / "b.sh")]


def test_runs_without_private_list_using_generic_patterns(tmp_path):
    (tmp_path / "a.py").write_text('URL = "http://192.168.77.2:8701"\n')
    assert [f.category for f in sweep.sweep([str(tmp_path)], [], [])] == ["ip_mac"]


def test_loopback_is_not_house_data(tmp_path):
    (tmp_path / "a.py").write_text('HOST = "127.0.0.1"\n')
    assert sweep.sweep([str(tmp_path)], [], []) == []


@pytest.mark.skipif(shutil.which("git") is None, reason="git missing in the python:slim test image; runs in GitHub CI and on HAOS")
def test_history_mode_finds_value_only_in_old_commit(tmp_path):
    g = ["git", "-C", str(tmp_path), "-c", "user.email=t@t", "-c", "user.name=t"]
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "a.py").write_text('OWNER = "Zelda"\n')
    subprocess.run(g + ["add", "."], check=True); subprocess.run(g + ["commit", "-qm", "1"], check=True)
    (tmp_path / "a.py").write_text('OWNER = "x"\n')
    subprocess.run(g + ["commit", "-qam", "2"], check=True)
    assert sweep.sweep([str(tmp_path)], ["Zelda"], []) == []
    assert sweep.sweep_history(str(tmp_path), ["Zelda"], [])


def test_escaped_mac_is_caught(tmp_path):
    (tmp_path / "a.py").write_text('return "02\\\\:00\\\\:5F\\\\:11\\\\:22\\\\:33\\n"\n')
    assert [f.category for f in sweep.sweep([str(tmp_path)], [], [])] == ["ip_mac"]


def test_allow_rule_never_hides_a_private_value(tmp_path):
    (tmp_path / "a.md").write_text("see github.com/joseluiz98 and Zelda\n")
    allow = sweep.parse_allow(["*:joseluiz98"])
    assert [f.category for f in sweep.sweep([str(tmp_path)], ["Zelda"], allow)] == ["person_name"]


def test_folder_named_after_a_resident_is_caught(tmp_path):
    (tmp_path / "zelda_notes").mkdir()
    (tmp_path / "zelda_notes" / "x.txt").write_text("nothing\n")
    found = sweep.sweep([str(tmp_path)], ["Zelda"], [])
    assert any(f.line is None and "zelda_notes" in f.path for f in found)


def test_common_api_key_shapes_are_secrets(tmp_path):
    (tmp_path / "k.env").write_text(
        "G=AIza" + "B" * 35 + "\n"
        "-----BEGIN PRIVATE KEY-----\n"
        "O=sk-" + "a" * 40 + "\n"
        "H=ghp_" + "c" * 36 + "\n"
        "P=github_pat_" + "d" * 60 + "\n"
        "E=sk_" + "e" * 48 + "\n")
    cats = [f.category for f in sweep.sweep([str(tmp_path)], [], [])]
    assert cats.count("secret") == 6


def test_private_value_inside_a_longer_word_is_not_a_hit(tmp_path):
    (tmp_path / "a.md").write_text("account joseluzelda98 and zeldas\n")
    assert sweep.sweep([str(tmp_path)], ["Zelda"], []) == []


def test_private_allow_rule_is_explicit(tmp_path):
    (tmp_path / "LICENSE").write_text("Copyright (c) 2026 Zelda\n")
    assert sweep.sweep([str(tmp_path)], ["Zelda"], sweep.parse_allow(["*/LICENSE:Copyright"]))
    assert sweep.sweep([str(tmp_path)], ["Zelda"], sweep.parse_allow(["private:*/LICENSE:^Copyright"])) == []


@pytest.mark.skipif(shutil.which("git") is None, reason="git missing in the python:slim test image; runs in GitHub CI and on HAOS")
def test_staged_mode_checks_the_index_not_the_working_tree(tmp_path):
    g = ["git", "-C", str(tmp_path), "-c", "user.email=t@t", "-c", "user.name=t"]
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "a.py").write_text('OWNER = "Zelda"\n')
    subprocess.run(g + ["add", "a.py"], check=True)
    (tmp_path / "a.py").write_text('OWNER = "x"\n')          # fixed only in the working tree
    assert sweep.sweep([str(tmp_path)], ["Zelda"], []) == []
    assert sweep.sweep_staged(str(tmp_path), ["Zelda"], [])


@pytest.mark.skipif(shutil.which("git") is None, reason="git missing in the python:slim test image; runs in GitHub CI and on HAOS")
def test_pre_push_scans_only_the_pushed_refs(tmp_path):
    """A finding on a branch that is not being pushed must not block the push of a clean branch (11/11)."""
    g = ["git", "-C", str(tmp_path), "-c", "user.email=t@t", "-c", "user.name=t"]
    subprocess.run(["git", "init", "-q", "-b", "main", str(tmp_path)], check=True)
    (tmp_path / "a.py").write_text('X = 1\n')
    subprocess.run(g + ["add", "."], check=True); subprocess.run(g + ["commit", "-qm", "1"], check=True)
    subprocess.run(g + ["checkout", "-qb", "other"], check=True)
    (tmp_path / "b.py").write_text('OWNER = "Zelda"\n')
    subprocess.run(g + ["add", "."], check=True); subprocess.run(g + ["commit", "-qm", "2"], check=True)
    rev = lambda r: subprocess.run(g + ["rev-parse", r], check=True, capture_output=True, text=True).stdout.strip()
    zero = "0" * 40
    main_line = f"refs/heads/main {rev('main')} refs/heads/main {zero}\n"
    other_line = f"refs/heads/other {rev('other')} refs/heads/other {zero}\n"
    assert sweep.pushed_revs(main_line) == [rev("main")]
    assert sweep.sweep_history(str(tmp_path), ["Zelda"], [], revs=sweep.pushed_revs(main_line)) == []
    assert sweep.sweep_history(str(tmp_path), ["Zelda"], [], revs=sweep.pushed_revs(other_line))
    # a deletion pushes nothing; the full history of a pushed ref is still scanned, not only the new commits
    assert sweep.pushed_revs(f"(delete) {zero} refs/heads/other {rev('other')}\n") == []
    assert sweep.sweep_history(str(tmp_path), ["Zelda"], []), "--history with no revs keeps scanning every ref"


class _CountingGit:
    """Records every process started through subprocess (run() goes through Popen too) and runs it for real."""

    def __init__(self, monkeypatch):
        self.calls = []
        real_popen = subprocess.Popen

        def popen(cmd, *a, **kw):
            self.calls.append(list(cmd))
            return real_popen(cmd, *a, **kw)

        monkeypatch.setattr(subprocess, "Popen", popen)


@pytest.mark.skipif(shutil.which("git") is None, reason="git missing in the python:slim test image; runs in GitHub CI and on HAOS")
def test_history_spawns_three_git_processes(make_repo, monkeypatch):
    commits = [{f"f{i}.txt": f"Zelda {i}\n" for i in range(25)}, {f"g{i}.txt": f"line {i}\n" for i in range(25)}]
    repo = make_repo(commits)
    git = _CountingGit(monkeypatch)
    found = sweep.sweep_history(repo, ["Zelda"], [])
    assert len(found) == 25
    assert [cmd[3] for cmd in git.calls] == ["rev-list", "cat-file", "cat-file"]
    assert git.calls[1][4].startswith("--batch-check") and git.calls[2][4] == "--batch"


def test_batch_reader_handles_trailing_newline_empty_and_missing():
    import io
    a, b, c, d = ("a" * 40, "b" * 40, "c" * 40, "d" * 40)
    stream = io.BytesIO(
        f"{a} blob 4\n".encode() + b"x\n\n\n" + b"\n"          # content "x\n\n\n", then the framing newline
        + f"{b} blob 0\n".encode() + b"\n"                      # empty blob
        + f"{c} missing\n".encode()                             # no body, no trailing newline
        + f"{d} blob 3\n".encode() + b"\nab" + b"\n")           # content starts with a newline
    assert list(sweep._parse_batch(stream, [a, b, c, d])) == [(a, b"x\n\n\n"), (b, b""), (c, None), (d, b"\nab")]
    assert stream.read() == b""
    with pytest.raises(RuntimeError):
        list(sweep._parse_batch(io.BytesIO(f"{b} blob 1\nz\n".encode()), [a]))            # header for another sha
    with pytest.raises(RuntimeError):
        list(sweep._parse_batch(io.BytesIO(f"{a} blob 9\nshort\n".encode()), [a]))        # truncated body


@pytest.mark.skipif(shutil.which("git") is None, reason="git missing in the python:slim test image; runs in GitHub CI and on HAOS")
def test_batch_history_matches_per_object_reading_on_awkward_blobs(make_repo):
    repo = make_repo([{"a.txt": "Zelda\n\n\n", "empty.txt": b"", "nl.txt": "\nZelda", "bin.dat": b"\x00Zelda"}])
    found = sweep.sweep_history(repo, ["Zelda"], [])
    assert sorted((f.path.split("@")[0], f.line) for f in found) == [("a.txt", 1), ("nl.txt", 2)]


@pytest.mark.skipif(shutil.which("git") is None, reason="git missing in the python:slim test image; runs in GitHub CI and on HAOS")
def test_batch_history_does_not_deadlock_on_many_and_large_blobs(make_repo):
    files = {f"d{i % 7}/f{i}.txt": f"row {i}\n" * 3 for i in range(1500)}
    files["big.txt"] = "filler line\n" * 40000 + "Zelda\n"
    repo = make_repo([files])
    found = sweep.sweep_history(repo, ["Zelda"], [])
    assert [(f.path.split("@")[0], f.line) for f in found] == [("big.txt", 40001)]


# --- whole-blob prefilter (plan 2026-11-21-sweep-speed, Task 3) ---------------------------------------------------

SEPARATORS = ["\n", "\r\n", "\r", "\x0b", "\x0c", "\x1c", "\x1d", "\x1e", "\x85", "\u2028", "\u2029"]


def _fragments():
    ip, email = "192" + ".168.4." + "7", "who" + "@" + "mail.example.org"
    mac = ":".join(["02", "00", "5E", "11", "22", "33"])
    mac_esc = "\\\\:".join(["02", "00", "5E", "11", "22", "33"])
    phone = "+55 11 9" + "1234-5678"
    bearer = "Bearer " + "example" + "q" * 20
    return [
        "Zelda", "zelda", "ZELDA", "Karlinha", "xzelda", "zeldax", "Zeldaé", "éZelda", "1Zelda2", "_Zelda_", "Zel da",
        "K" + "arlinha", "ſ", "İ", "ß", "é", "a", "bb", "-", "_", ".", " ", "\t", "word", "def f(x):", "    return 1",
        ip, ip + "9", "x" + ip, mac, mac_esc, "x" + mac, email, "x" + email, phone, "+55 11", "91234-5678", "+55\n", "11 ",
        "+55\x0c11\x1c91234-5678", '"token"', ":", '"' + "t" * 20 + '"', '"token"\n:\n"' + "t" * 20 + '"', bearer,
        "Bearer short", "/home/zelda", "/home/jose", "/home/josefa", "/home/jose_x", "/home/" + "Zelda", "/api/webhook/abcdefgh12",
        "https://silva" + "software.example/z", "AIza" + "B" * 35, "sk-" + "a" * 40, "ghp_" + "c" * 36,
        "-----BEGIN " + "PRIVATE KEY-----", "eyJ" + "a" * 25,
    ] + SEPARATORS * 3


def test_prefilter_never_drops_a_line_hit(ref):
    import random
    rng = random.Random(20261121)
    frags = _fragments()
    private = [("person_name", "Zelda"), ("person_name", "Karlinha"), ("device_id", "Zel da"), ("person_name", ""),
               ("person_name", "ſ"), ("person_name", "Zelda")]
    allow_lines = ["*/docs/*:Bearer example", "*/docs/*:Zelda", "private:*/LICENSE:Zelda", "*:^def", "*/x/*:."]
    a_new, a_ref = sweep.parse_allow(allow_lines), ref.parse_allow(allow_lines)
    pv_new, pv_ref = sweep._private(private), ref._private(private)
    paths = [("/r/a.py", "a.py"), ("/r/docs/n.md", "docs/n.md"), ("/r/LICENSE", "LICENSE"), ("/r/x/y.txt", "x/y.txt"),
             ("/r/Zelda/n.txt", "Zelda/n.txt"), ("/r/ſ/zelda_karlinha.txt", "ſ/zelda_karlinha.txt"), ("/r/p.txt", None)]
    hits = 0
    for i in range(2000):
        text = "".join(rng.choice(frags) for _ in range(rng.randint(0, 40)))
        path, rel = paths[i % len(paths)]
        expect = ref._scan_text(path, text, pv_ref, a_ref, rel)
        assert sweep._scan_text(path, text, pv_new, a_new, rel) == expect, repr(text)
        assert sweep._scan_text(path, text, private, a_new, rel) == expect, "old (cat, val) pairs must still work"
        hits += bool(expect)
    assert hits > 1000, "the fragments must produce findings in most texts"


def test_clean_blob_runs_no_line_regex(monkeypatch):
    calls = []
    real = sweep._allowed
    monkeypatch.setattr(sweep, "_allowed", lambda *a, **k: calls.append(a) or real(*a, **k))
    text = "def f(x):\n    return x + 1  # nothing to see\n" * 25000
    assert len(text) > 1_000_000
    pv = sweep._private(["Zelda", "Karlinha"])
    assert sweep._scan_text("/r/a.py", text, pv, sweep.parse_allow(["*:^def"]), "a.py") == []
    assert calls == []
    # a blob with one hit evaluates the allow rules for that line only
    assert len(sweep._scan_text("/r/a.py", text + "who" + "@" + "mail.example.org\n", pv, [], "a.py")) == 1


def test_separators_are_not_letters_or_word_characters():
    """The prefilter relies on it: a line edge looks the same inside the whole text as inside the line."""
    for c in SEPARATORS:
        assert not re.fullmatch(r"\w", c)
        assert not re.match(f"[{sweep.LETTER}]", c, re.I)
        assert len(("a" + c + "b").splitlines()) == 2


def test_no_anchor_in_patterns():
    """A line-anchored pattern would make the whole-text prefilter unsound (it could drop a real line hit)."""
    pats = [p for pats in sweep.GENERIC.values() for p in pats]
    assert len(pats) >= 10
    for pat in pats:
        src, in_class, i = pat.pattern, False, 0
        while i < len(src):
            c = src[i]
            if c == "\\":
                assert src[i + 1] not in "AZB", f"{src}: \\{src[i + 1]} is position-dependent"
                i += 2
                continue
            if in_class:
                in_class = c != "]"
            elif c == "[":
                in_class = True
            else:
                assert c not in "^$", f"{src}: anchor outside a class"
            i += 1
        assert not (pat.flags & re.M), src
        assert "(?m" not in src and "(?s" not in src and "(?a" not in src
