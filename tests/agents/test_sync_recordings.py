"""Nightly upload (satellite/sync/sync_recordings.sh): the VM host key is pinned (plan 2026-11-18 Task 3).

`ssh`, `rsync`, `nice`, `ionice` and `sleep` are fakes on PATH; HOME and TMPDIR are the test's own folders."""
import os
import stat
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "satellite" / "sync" / "sync_recordings.sh"

FAKES = {
    "rsync": '#!/bin/bash\nprintf "%s\\n" "$*" >> "$FAKE_LOG"\nexit "${FAKE_RC:-0}"\n',
    "ssh": '#!/bin/bash\necho "ssh $*" >> "$FAKE_LOG"\nexit 0\n',
    "nice": '#!/bin/bash\nshift 2\nexec "$@"\n',  # nice -n 19 <cmd...>
    "ionice": '#!/bin/bash\nshift 1\nexec "$@"\n',  # ionice -c3 <cmd...>
    "sleep": "#!/bin/bash\nexit 0\n",
}


def env_for(tmp_path, rc=0, known_hosts=True, legacy_known_hosts=False):
    home, bins, tmp = tmp_path / "home", tmp_path / "bin", tmp_path / "tmp"
    for d in (home / ".ssh", home / "gravacoes", home / "vozes", bins, tmp):
        d.mkdir(parents=True, exist_ok=True)
    for name, body in FAKES.items():
        p = bins / name
        p.write_text(body)
        p.chmod(p.stat().st_mode | stat.S_IXUSR)
    if known_hosts:
        (home / ".ssh" / "known_hosts_satsync").write_text("vm.test ssh-ed25519 AAAA\n")
    if legacy_known_hosts:
        (home / ".ssh" / "known_hosts").write_text("vm.test ssh-ed25519 BBBB\n")
    return home, {"HOME": str(home), "TMPDIR": str(tmp), "PATH": f"{bins}:/usr/bin:/bin",
                  "SATSYNC_VM": "@".join(("satsync", "vm.test")), "FAKE_LOG": str(tmp_path / "calls.log"), "FAKE_RC": str(rc)}


def run(env):
    return subprocess.run(["bash", str(SCRIPT)], env=env, capture_output=True, text=True, timeout=60)


def calls(tmp_path):
    log = tmp_path / "calls.log"
    return log.read_text().splitlines() if log.exists() else []


def test_without_the_pinned_host_key_it_stops_with_rc_3_and_never_connects(tmp_path):
    home, env = env_for(tmp_path, known_hosts=False)
    r = run(env)
    assert r.returncode == 3
    assert "known_hosts_satsync" in r.stderr
    assert calls(tmp_path) == []
    assert not (home / ".friday_sync_ok").exists()


def test_upload_pins_the_host_key_and_stamps_only_when_everything_went_through(tmp_path):
    home, env = env_for(tmp_path)
    r = run(env)
    assert r.returncode == 0, r.stdout + r.stderr
    got = calls(tmp_path)
    assert len(got) == 3  # gravacoes, vozes and the `.sync_ok` marker
    for line in got:
        assert f"UserKnownHostsFile={home}/.ssh/known_hosts_satsync" in line
        assert "StrictHostKeyChecking=yes" in line
        assert "accept-new" not in line
    assert (home / ".friday_sync_ok").is_file()
    assert not (home / "gravacoes" / ".friday_sync_ok").exists()


def test_a_failed_upload_leaves_no_stamp(tmp_path):
    home, env = env_for(tmp_path, rc=23)
    stamp = home / ".friday_sync_ok"
    stamp.write_text("")
    os.utime(stamp, (1_000_000, 1_000_000))
    r = run(env)
    assert r.returncode == 23
    assert stamp.stat().st_mtime == 1_000_000
    assert not list((tmp_path / "tmp").iterdir())  # the marker folder was not even created


def test_a_satellite_that_already_trusts_the_vm_keeps_uploading_before_the_key_is_pinned(tmp_path):
    """The office satellite trusted the VM by hand (~/.ssh/known_hosts): until HA pins the key, that file is still a
    verified source (StrictHostKeyChecking=yes), so the update does not cost a night of uploads."""
    home, env = env_for(tmp_path, known_hosts=False, legacy_known_hosts=True)
    r = run(env)
    assert r.returncode == 0, r.stdout + r.stderr
    got = calls(tmp_path)
    assert len(got) == 3
    for line in got:
        assert f"GlobalKnownHostsFile={home}/.ssh/known_hosts" in line
        assert "StrictHostKeyChecking=yes" in line
        assert "accept-new" not in line
