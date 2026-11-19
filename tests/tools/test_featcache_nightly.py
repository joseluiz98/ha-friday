"""featcache_nightly.sh (plan 2026-11-18 per-satellite recordings, Task 4): every satellite in incoming/ is cached,
one day at a time, named amb<YYYYMMDD>_<sat>; per-satellite retention and registry; global stamp only when all OK."""
import datetime as dt
import json
import os
import pathlib
import subprocess

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools/wakeword/wwt/featcache_nightly.sh"
SAT_A, SAT_B = "sat-0a1b2c", "sat-1d2e3f"

# Fake docker: finds the /fc mount and the cache name in the `featcache.py cache /fc <name> ...` command, logs the
# call, fails when the name is listed in FAKE_DOCKER_FAIL, otherwise writes <name>.json and <name>.emb.npy.
FAKE_DOCKER = r"""#!/bin/sh
fc=""; cmd=""; prev=""
for a in "$@"; do
  case "$prev" in -v) case "$a" in *:/fc) fc=${a%:/fc} ;; esac ;; -c) cmd=$a ;; esac
  prev=$a
done
name=$(echo "$cmd" | sed -nE 's#.*featcache.py cache /fc ([^ ]+) .*#\1#p')
echo "$name" >> "$FAKE_LOG"
case " $FAKE_DOCKER_FAIL " in *" $name "*) echo boom; exit 1 ;; esac
echo '{"ok": true}' > "$fc/$name.json"; : > "$fc/$name.emb.npy"
"""
FAKE_OC = """#!/bin/sh
echo "$*" >> "$FAKE_OC_LOG"
"""


def day(n: int) -> str:
    return (dt.date.today() - dt.timedelta(days=n)).strftime("%Y%m%d")


def _setup(tmp_path):
    bin_ = tmp_path / "bin"; bin_.mkdir()
    for name, body in (("docker", FAKE_DOCKER), ("oc", FAKE_OC)):
        (bin_ / name).write_text(body); (bin_ / name).chmod(0o755)
    code = tmp_path / "code"; code.mkdir(); (code / "featcache.py").write_text("")
    dirs = {k: tmp_path / k for k in ("incoming", "cache", "state")}
    for d in dirs.values():
        d.mkdir()
    env = {**os.environ, "PATH": f"{bin_}:{os.environ['PATH']}", "FRIDAY_WAKEWORD_INCOMING": str(dirs["incoming"]),
           "FRIDAY_WWT_CACHE": str(dirs["cache"]), "FRIDAY_WWT_STATE": str(dirs["state"]),
           "FRIDAY_WWT_CODE": str(code), "FRIDAY_LOCAL_CONFIG": str(tmp_path / "no-local.env"),
           "FRIDAY_TELEGRAM_CHAT": "123", "FAKE_LOG": str(tmp_path / "docker.log"),
           "FAKE_OC_LOG": str(tmp_path / "oc.log"), "FAKE_DOCKER_FAIL": ""}
    return dirs, env


def _wav(incoming, sat, d, hhmmss="101500"):
    a = incoming / sat / "gravacoes" / "ambiente"; a.mkdir(parents=True, exist_ok=True)
    (a / f"ambiente_{d}_{hhmmss}.wav").write_bytes(b"RIFF")
    return a


def _run(env):
    return subprocess.run(["bash", str(SCRIPT)], env=env, capture_output=True, text=True)


def _registry(state):
    return json.loads((state / "wwt-featcache.json").read_text())


def test_two_satellites_cached_with_per_satellite_names(tmp_path):
    dirs, env = _setup(tmp_path)
    _wav(dirs["incoming"], SAT_A, day(1)); _wav(dirs["incoming"], SAT_A, day(2))
    _wav(dirs["incoming"], SAT_B, day(3)); _wav(dirs["incoming"], SAT_B, day(0))  # today: not cached yet
    r = _run(env)
    assert r.returncode == 0, r.stdout + r.stderr
    names = sorted(p.name for p in dirs["cache"].glob("*.json"))
    assert names == sorted([f"amb{day(1)}_{SAT_A}.json", f"amb{day(2)}_{SAT_A}.json", f"amb{day(3)}_{SAT_B}.json"])
    assert (dirs["state"] / "wwt-featcache.ok").exists()
    reg = _registry(dirs["state"])
    assert set(reg) == {SAT_A, SAT_B}
    for v in reg.values():
        assert dt.datetime.fromisoformat(v).tzinfo is not None
    # already cached days are not extracted again
    calls = (tmp_path / "docker.log").read_text().split()
    r = _run(env)
    assert r.returncode == 0 and (tmp_path / "docker.log").read_text().split() == calls


def test_satellite_without_ambient_folder_counts_as_a_pass(tmp_path):
    dirs, env = _setup(tmp_path)
    (dirs["incoming"] / SAT_B).mkdir()  # paired, nothing uploaded yet
    (dirs["incoming"] / SAT_B / ".sync_ok").write_text("x")
    _wav(dirs["incoming"], SAT_A, day(1))
    r = _run(env)
    assert r.returncode == 0, r.stdout + r.stderr
    assert set(_registry(dirs["state"])) == {SAT_A, SAT_B}
    assert (dirs["state"] / "wwt-featcache.ok").exists()


def test_retention_and_cache_cleanup_are_per_satellite(tmp_path):
    dirs, env = _setup(tmp_path)
    old = day(40)
    a = _wav(dirs["incoming"], SAT_A, old)
    _wav(dirs["incoming"], SAT_B, day(5))
    c = dirs["cache"]
    for n in (f"amb{old}_{SAT_A}", f"amb{day(5)}_{SAT_B}", f"amb{day(6)}_{SAT_B}"):
        (c / f"{n}.json").write_text("{}"); (c / f"{n}.vad.npy").write_text("")
    # a cache of SAT_A for a day only SAT_B has audio for: it goes (the cache follows the satellite's own audio)
    (c / f"amb{day(5)}_{SAT_A}.json").write_text("{}")
    r = _run(env)
    assert r.returncode == 0, r.stdout + r.stderr
    assert not list(a.glob("*.wav"))  # > 30 days: deleted
    left = sorted(p.name for p in c.iterdir())
    assert left == sorted([f"amb{day(5)}_{SAT_B}.json", f"amb{day(5)}_{SAT_B}.vad.npy"])


def test_registry_is_merged_not_replaced(tmp_path):
    dirs, env = _setup(tmp_path)
    (dirs["state"] / "wwt-featcache.json").write_text(json.dumps({"sat-999999": "2026-01-01T00:00:00+00:00"}))
    _wav(dirs["incoming"], SAT_A, day(1))
    assert _run(env).returncode == 0
    reg = _registry(dirs["state"])
    assert reg["sat-999999"] == "2026-01-01T00:00:00+00:00" and SAT_A in reg


def test_extractor_failure_stops_without_global_stamp_or_new_entry(tmp_path):
    dirs, env = _setup(tmp_path)
    _wav(dirs["incoming"], SAT_A, day(1))
    _wav(dirs["incoming"], SAT_B, day(1))
    env["FAKE_DOCKER_FAIL"] = f"amb{day(1)}_{SAT_B}"
    r = _run(env)
    assert r.returncode != 0
    assert not (dirs["state"] / "wwt-featcache.ok").exists()
    reg = _registry(dirs["state"])
    assert SAT_A in reg and SAT_B not in reg
    left = {p.name for p in dirs["cache"].glob(f"amb{day(1)}_{SAT_B}*")}
    assert left == {f"amb{day(1)}_{SAT_B}.log"}  # half-built cache removed, the log stays for the message
    msg = (tmp_path / "oc.log").read_text()
    assert "telegram" in msg and SAT_B in msg and day(1) in msg
