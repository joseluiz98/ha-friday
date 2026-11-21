"""Differential tests: the optimised scripts/sweep.py must return exactly what the frozen reference returns
(plan 2026-11-21-sweep-speed). Fixtures build every sensitive-looking string at runtime, so this file is clean."""
import os
import pathlib
import shutil
import subprocess

import pytest

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git missing in the python:slim test image")

ROOT = pathlib.Path(__file__).resolve().parents[2]
PRIVATE = ["Zelda", "Karlinha"]
ALLOW_LINES = ["*/docs/*:Bearer example", "private:*/LICENSE:Zelda"]


def _history_files():
    ip = "192" + "." + "168" + ".1.5"
    mac = "02:00:5E:11:22:33"
    mac_esc = "\\\\:".join(["02", "00", "5E", "11", "22", "33"])
    email = "someone" + "@" + "example.com"
    phone = "+55 11 9" + "1234-5678"
    bearer = "Bearer " + "example" + "x" * 20
    first = {
        "a.py": 'OWNER = "Zelda"\nOTHER = "karlinha"\nx = 1\n',
        "Zelda/notes.txt": "nothing in here\n",
        "kar_Karlinha_file.txt": "clean\n",
        "gen.env": "\n".join([
            "IP=" + ip, "MAC=" + mac, "MACE=" + mac_esc, "MAIL=" + email, "TEL=" + phone,
            "HOME=/home/zelda/x", "OK=/home/jose/y", "HOOK=/api/webhook/abcdefgh12345",
            "URL=https://silva" + "software.example/x", "K=AIza" + "B" * 35, "TOK=" + '"token": "' + "t" * 20 + '"',
            "G=ghp_" + "c" * 36, "SK=sk-" + "a" * 40, ""]),
        "docs/allow.md": bearer + "\n" + "other line " + bearer + " Zelda\nplain\n",
        "docs/nope.txt": bearer + "\n",
        "LICENSE": "Copyright Zelda\nZelda again\n",
        "bin.dat": b"\x00\x01binary " + b"Zelda " + ("sk-" + "a" * 40).encode() + b"\n",
        "tmp.txt": "secret " + "sk-" + "b" * 40 + "\nZelda\n",
        "dup1.txt": "same " + ip + "\n",
        "dup2.txt": "same " + ip + "\n",
        "crlf.txt": "a\r\nZelda\r\n" + ip + "\r\nb\r\n",
        "sep.txt": "x\x0cZelda\u2028y\x85 " + ip + "\x1c" + email + "\x0bKarlinha\x1d\x1e done\u2029Zelda\n",
        "phone_split.txt": "+55 11\n91234-5678\n",
        "phone_ws.txt": "+55\n11 91234-5678\n",
        "token_split.txt": '"token"\n:\n"' + "z" * 20 + '"\n',
        "empty.txt": b"",
        "latin1.txt": b"\xff\xfeZelda\xe9 Karlinha\n",
        "glued.txt": "xzelday98 zeldas Zelda.\n",
    }
    second = {
        "a.py": 'OWNER = "x"\nx = 1\n',
        "tmp.txt": None,
        "new/dir/more.txt": "line\nZelda\n" + ip + "\n",
        "dup3.txt": "same " + ip + "\n",
        "Karlinha": "folder-or-file named after a resident\n",
    }
    return [first, second]


@pytest.fixture
def repo(make_repo):
    return make_repo(_history_files())


def test_reference_matches_current_on_fixture_repo(new, ref, repo, branch):
    P, A = PRIVATE, new.parse_allow(ALLOW_LINES)
    a = ref.parse_allow(ALLOW_LINES)
    expect = ref.sweep_history(repo, P, a)
    assert expect, "the fixture must produce findings"
    assert {f.category for f in expect} >= {"secret", "person_name", "ip_mac", "email_phone", "personal_path", "url_webhook"}
    assert new.sweep_history(repo, P, A) == expect

    sha = branch(repo, "other", {"o.txt": "Zelda " + "192" + ".168.9.9\n", "docs/b.md": "Bearer " + "example" + "y" * 21 + "\n"},
                 base="HEAD~1")
    for revs in ([sha], ["main"], [sha, "main"], []):
        assert new.sweep_history(repo, P, A, revs) == ref.sweep_history(repo, P, a, revs)
    assert new.sweep_history(repo, [], []) == ref.sweep_history(repo, [], [])

    assert new.sweep([repo], P, A) == ref.sweep([repo], P, a)
    assert new.sweep([repo], [], []) == ref.sweep([repo], [], [])


def test_cached_runs_match_the_reference_cold_warm_and_after_new_commits(new, ref, repo, make_repo, branch, tmp_path):
    P, A, a = PRIVATE, new.parse_allow(ALLOW_LINES), ref.parse_allow(ALLOW_LINES)
    cache = str(tmp_path / "cache" / "k")
    expect = ref.sweep_history(repo, P, a)
    assert new.sweep_history(repo, P, A, cache=cache) == expect            # cold
    assert new.sweep_history(repo, P, A, cache=cache) == expect            # warm
    sha = branch(repo, "more", {"x.txt": "fresh Zelda\n", "y.txt": "clean\n"})
    assert new.sweep_history(repo, P, A, cache=cache) == ref.sweep_history(repo, P, a)
    assert new.sweep_history(repo, P, A, [sha], cache=cache) == ref.sweep_history(repo, P, a, [sha])
    assert new.sweep_history(repo, P + ["fresh"], A, cache=cache + "2") == ref.sweep_history(repo, P + ["fresh"], a)


def test_reference_matches_current_on_staged_tree(new, ref, make_repo):
    repo = make_repo([{"base.txt": "ok\n"}])
    files = _history_files()[0]
    for rel, content in files.items():
        p = pathlib.Path(repo) / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(content if isinstance(content, bytes) else content.encode())
    subprocess.run(["git", "-C", repo, "add", "-A"], check=True)
    # a staged value fixed only in the working tree must still be seen in the index
    (pathlib.Path(repo) / "a.py").write_text("clean = 1\n")
    A, a = new.parse_allow(ALLOW_LINES), ref.parse_allow(ALLOW_LINES)
    expect = ref.sweep_staged(repo, PRIVATE, a)
    assert expect
    assert new.sweep_staged(repo, PRIVATE, A) == expect
    assert new.sweep_staged(repo, [], []) == ref.sweep_staged(repo, [], [])


def test_main_prints_the_same_text_and_exit_code(new, ref, repo, tmp_path, monkeypatch, capsys):
    lst = tmp_path / "private.txt"
    lst.write_text("\n".join(PRIVATE) + "\n")
    monkeypatch.setenv("FRIDAY_SENSITIVE_LIST", str(lst))
    monkeypatch.setenv("FRIDAY_SWEEP_NO_CACHE", "1")
    outs = []
    for mod in (ref, new):
        rc = mod.main(["--history", repo])
        outs.append((rc, capsys.readouterr().out))
    assert outs[0] == outs[1]
    assert outs[0][0] == 1


@pytest.mark.slow
@pytest.mark.skipif(os.environ.get("FRIDAY_SWEEP_EQUIV") != "1", reason="slow: set FRIDAY_SWEEP_EQUIV=1")
def test_reference_matches_current_on_this_repo(new, ref):
    """Whole history of this repo, with the real private list (when present) and with a 30-word synthetic list that
    yields thousands of findings. Prints only counts, never values."""
    lists = [[("person_name", w) for w in
              "def self friday satellite return import class none true false str int len list dict test assert "
              "config path name data value error import from with for while else".split()[:30]]]
    real = os.environ.get("FRIDAY_SENSITIVE_LIST", new.DEFAULT_LIST)
    if os.path.exists(real):
        lists.append(new.scan.load_known(real))
    allow_file = ROOT / ".sweep-allow"
    allow_lines = allow_file.read_text(encoding="utf-8").splitlines() if allow_file.exists() else []
    for pv in lists:
        want = ref.sweep_history(str(ROOT), pv, ref.parse_allow(allow_lines))
        got = new.sweep_history(str(ROOT), pv, new.parse_allow(allow_lines))
        same = got == want
        assert same, f"findings differ: new={len(got)} reference={len(want)} (list of {len(pv)} values)"
        print(f"equivalence: {len(want)} findings, list of {len(pv)} values")
