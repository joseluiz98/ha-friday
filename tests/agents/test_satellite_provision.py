"""Provisioning commands of the satellite agent: bundles, onboarding chain, two-level reset."""

import asyncio
import hashlib
import io
import json
import os
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest
from aiohttp import web

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "agents" / "satellite"))
from friday_satellite_agent import capabilities as cap  # noqa: E402
from friday_satellite_agent import provision as pv  # noqa: E402

HELPER = Path(__file__).resolve().parents[2] / "agents" / "satellite" / "friday-agent-apply"


class FakeProgram:
    def __init__(self, ok=True):
        self.calls, self.ok, self.cfg = [], ok, {"verificador": None}

    async def command(self, body):
        self.calls.append(body)
        return {"ok": self.ok}

    async def get_config(self):
        return dict(self.cfg)

    async def put_config(self, changes):
        self.cfg.update(changes)
        return dict(self.cfg)


def tar_bytes(files: dict) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as t:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            t.addfile(info, io.BytesIO(data))
    return buf.getvalue()


UPDATE = {
    "program/friday_satelite.py": b"NEW MAIN",
    "program/maestro.py": b"NEW MAESTRO",
    "program/vendor/xvf_host.py": b"VENDOR",
    "program/sons/onboarding/ready.wav": b"WAV",
    "agent/friday_satellite_agent/__main__.py": b"AGENT",
    "agent/friday_agent_core/__init__.py": b"CORE",
}
VOICES = {"vozes/ana/jabra-speak-410/a.wav": b"A", "vozes/bia/xvf3800/b.wav": b"B", "friday_verificador.pkl": b"PKL"}


@pytest.fixture
def env(tmp_path):
    for d in ("etc", "ssh", "program", "voices", "models"):
        (tmp_path / d).mkdir()
    (tmp_path / "etc" / "mic").write_text("xvf3800\n")
    (tmp_path / "etc" / "config.json").write_text(json.dumps({"senha_porta_local": "pw-secret", "comodo": "Sala"}))
    (tmp_path / "ssh" / "id_satsync.pub").write_text("ssh-ed25519 AAAA sat-1-satsync\n")
    (tmp_path / "program" / "maestro.py").write_text("OLD MAESTRO")
    return tmp_path


def make(env, payloads=None, program=None, restart=None, exits=None, agent_applied=None):
    payloads = payloads if payloads is not None else {}
    program = program or FakeProgram()
    exits = exits if exits is not None else []
    agent_applied = agent_applied if agent_applied is not None else []

    async def fetch(url, dest):
        data = payloads[url]
        Path(dest).write_bytes(data)
        return hashlib.sha256(data).hexdigest()

    async def restart_program():
        return {"ok": True}

    async def apply_agent(stage):
        agent_applied.append(sorted(os.listdir(stage)))
        return 0

    async def fast(_):
        return None

    return pv.Provisioner(
        loop_sleep=fast,
        etc_dir=str(env / "etc"), config_path=str(env / "etc" / "config.json"), ssh_dir=str(env / "ssh"),
        program_dir=str(env / "program"), bundles_dir=str(env / "bundles"), voices_dir=str(env / "voices"),
        models_dir=str(env / "models"), program=program, restart_program=restart or restart_program,
        apply_agent=apply_agent, fetch=fetch, exit_fn=exits.append, ip_fn=lambda: "192.0.2.50",
    ), program, exits, agent_applied


def sha(b):
    return hashlib.sha256(b).hexdigest()


async def test_provision_info(env):
    p, *_ = make(env)
    r = await p.info()
    assert r["ok"]
    d = r["data"]
    assert d["program_password"] == "pw-secret" and d["mic"] == "xvf3800" and d["ip"] == "192.0.2.50"
    assert d["sync_pubkey"].startswith("ssh-ed25519 ") and d["versions"]["agent"] == cap.VERSION
    assert d["versions"]["program"]
    (env / "program" / "VERSION").write_text("2026.11.5\n")
    assert (await p.info())["data"]["versions"]["program"] == "2026.11.5"


async def test_update_copies_files_and_keeps_bak(env):
    blob = tar_bytes(UPDATE)
    p, program, exits, applied = make(env, {"http://vm/b": blob})
    r = await p.apply_bundle("update", "http://vm/b", sha(blob))
    assert r == {"ok": True, "data": {"program_restart": "done"}}
    prog = env / "program"
    assert (prog / "friday_satelite.py").read_text() == "NEW MAIN"
    assert (prog / "maestro.py").read_text() == "NEW MAESTRO"
    baks = [n for n in os.listdir(prog) if n.startswith("maestro.py.bak.")]
    assert len(baks) == 1 and (prog / baks[0]).read_text() == "OLD MAESTRO"
    assert (prog / "vendor" / "xvf_host.py").read_text() == "VENDOR"
    assert (prog / "sons" / "onboarding" / "ready.wav").read_bytes() == b"WAV"
    assert applied == [["friday_agent_core", "friday_satellite_agent"]]
    await p._exit_task
    assert exits == [3]  # the agent restarts itself last
    assert not any(n.startswith(("bundle-", "stage-")) for n in os.listdir(env / "bundles"))  # cleaned up


async def test_wrong_sha_applies_nothing(env):
    blob = tar_bytes(UPDATE)
    p, _, exits, applied = make(env, {"http://vm/b": blob})
    r = await p.apply_bundle("update", "http://vm/b", sha(b"other"))
    assert r == {"ok": False, "error": "sha256"}
    assert (env / "program" / "maestro.py").read_text() == "OLD MAESTRO"
    assert applied == [] and exits == []


async def test_invalid_layout_applies_nothing(env):
    blob = tar_bytes({"program/maestro.py": b"X"})  # no friday_satelite.py, no agent
    p, *_ = make(env, {"http://vm/b": blob})
    r = await p.apply_bundle("update", "http://vm/b", sha(blob))
    assert r == {"ok": False, "error": "layout"}
    assert (env / "program" / "maestro.py").read_text() == "OLD MAESTRO"
    blob = tar_bytes({"nothing.txt": b"x"})
    p, *_ = make(env, {"http://vm/c": blob})
    assert (await p.apply_bundle("voices", "http://vm/c", sha(blob))) == {"ok": False, "error": "layout"}


async def test_bad_arguments(env):
    p, *_ = make(env)
    assert (await p.apply_bundle("firmware", "http://vm/b", "0" * 64))["error"] == "kind"
    assert (await p.apply_bundle("update", "file:///etc/passwd", "0" * 64))["error"] == "url"
    assert (await p.apply_bundle("update", "http://vm/b", "xyz"))["error"] == "sha256"


async def test_unsafe_archive_member_is_rejected(env):
    blob = tar_bytes({"../escape.txt": b"x"})
    p, *_ = make(env, {"http://vm/b": blob})
    r = await p.apply_bundle("update", "http://vm/b", sha(blob))
    assert r["ok"] is False and r["error"] == "extract"
    assert not (env / "escape.txt").exists() and not (env.parent / "escape.txt").exists()


async def test_download_failure_does_not_leak_url(env):
    p, *_ = make(env, {})
    r = await p.apply_bundle("update", "http://vm/secret-token", "0" * 64)
    assert r == {"ok": False, "error": "download"}


async def test_voices_copies_tree_and_verifier(env):
    blob = tar_bytes(VOICES)
    p, program, *_ = make(env, {"http://vm/v": blob})
    r = await p.apply_bundle("voices", "http://vm/v", sha(blob))
    assert r["ok"]
    assert (env / "voices" / "ana" / "jabra-speak-410" / "a.wav").read_bytes() == b"A"
    assert (env / "voices" / "bia" / "xvf3800" / "b.wav").read_bytes() == b"B"
    assert (env / "models" / "friday_verificador.pkl").read_bytes() == b"PKL"
    assert program.cfg["verificador"] == str(env / "models" / "friday_verificador.pkl")


async def test_voices_does_not_override_an_existing_verifier_setting(env):
    blob = tar_bytes(VOICES)
    program = FakeProgram()
    program.cfg["verificador"] = "/custom.pkl"
    p, *_ = make(env, {"http://vm/v": blob}, program=program)
    await p.apply_bundle("voices", "http://vm/v", sha(blob))
    assert program.cfg["verificador"] == "/custom.pkl"


async def test_restart_waits_for_conversation_to_close(env):
    blob = tar_bytes(UPDATE)
    answers = iter([{"ok": False, "error": "conversation_open"}, {"ok": True}])

    async def restart():
        return next(answers)

    async def nosleep(_):
        return None

    p, *_ = make(env, {"http://vm/b": blob}, restart=restart)
    p._sleep = nosleep
    r = await p.apply_bundle("update", "http://vm/b", sha(blob))
    assert r["data"]["program_restart"] == "done"
    await p._exit_task


async def test_start_onboarding_chains_the_two_program_commands(env):
    p, program, *_ = make(env)
    facts = {"areas": [{"id": "sala", "name": "Sala"}], "known_voices": ["Ana"], "failed_steps": []}
    assert (await p.start_onboarding(facts)) == {"ok": True}
    assert program.calls == [{"acao": "wake_word_test", "timeout_s": 30}]
    await p.on_onboarding("wake_word_ok")
    assert program.calls[1] == {"acao": "open_presentation", "facts": dict(facts, wake_word_ok=True)}
    await p.on_onboarding("wake_word_ok")  # only once
    assert len(program.calls) == 2


async def test_start_onboarding_failed_test_still_opens_presentation(env):
    p, program, *_ = make(env)
    await p.start_onboarding({"areas": []})
    await p.on_onboarding("area_selected")  # not a wake-word result: ignored
    assert len(program.calls) == 1
    await p.on_onboarding("wake_word_failed")
    assert program.calls[1]["facts"]["wake_word_ok"] is False


async def test_start_onboarding_program_refuses(env):
    p, *_ = make(env, program=FakeProgram(ok=False))
    assert (await p.start_onboarding({"areas": []}))["ok"] is False
    assert (await p.start_onboarding("nope"))["ok"] is False


def with_run(p, listing="", log=None, up_code=0):
    log = log if log is not None else []

    async def run(argv):
        log.append(argv)
        if argv[:2] == ["nmcli", "-t"]:
            return 0, listing
        return (up_code if "up" in argv else 0), ""

    p._run = run
    return log


LISTING = ("ExampleNet:802-11-wireless\nfriday-wifi-1700000000:802-11-wireless\n"
           "friday-wifi-1700000100:802-11-wireless\nWired connection 1:802-3-ethernet\nlo:loopback\n")


async def test_reset_level_2_leaves_pairable_without_token(env):
    (env / "etc" / "ha_token").write_text("tok")
    p, program, exits, _ = make(env)
    r = await p.factory_reset(2)
    assert r == {"ok": True}
    assert (env / "etc" / "pairable").exists() and not (env / "etc" / "ha_token").exists()
    cfg = json.loads((env / "etc" / "config.json").read_text())
    assert cfg["comodo"] is None and cfg["senha_porta_local"] == "pw-secret"
    assert (env / "etc" / "config.json").stat().st_mode & 0o777 == 0o600
    assert program.calls == [{"acao": "play_phrases", "keys": ["reset_home"]}]
    await p._exit_task
    assert exits == [3]


async def test_reset_level_2_wipes_personal_data_only(env):
    (env / "voices" / "ana").mkdir()
    (env / "voices" / "ana" / "a.wav").write_bytes(b"x")
    (env / "voices" / "loose.wav").write_bytes(b"x")
    (env / "models").mkdir(exist_ok=True)
    (env / "models" / pv.VERIFIER).write_bytes(b"pkl")
    (env / "models" / "friday.onnx").write_bytes(b"model")
    (env / "gravacoes" / "ativacoes").mkdir(parents=True)
    (env / "gravacoes" / "ativacoes" / "1.wav").write_bytes(b"x")
    p, *_ = make(env)
    p.voices_dir = str(env / "voices")
    p._wipe_paths = [str(env / "voices" / "*"), str(env / "models" / pv.VERIFIER), str(env / "gravacoes" / "*")]
    assert (await p.factory_reset(2)) == {"ok": True}
    assert os.listdir(env / "voices") == [] and os.listdir(env / "gravacoes") == []
    assert not (env / "models" / pv.VERIFIER).exists() and (env / "models" / "friday.onnx").exists()
    await p._exit_task


async def test_reset_level_1_goes_back_to_the_original_network(env):
    p, program, exits, _ = make(env)
    log = with_run(p, LISTING)
    assert (await p.factory_reset(1)) == {"ok": True}
    helper = pv.WIFI_HELPER
    assert [*helper, "delete", "friday-wifi-1700000000"] in log and [*helper, "delete", "friday-wifi-1700000100"] in log
    assert log[-1] == [*helper, "up", "ExampleNet"]
    assert [c for c in log if "delete" in c and "ExampleNet" in c] == []
    assert program.calls == [{"acao": "play_phrases", "keys": ["reset_network"]}]
    assert not (env / "etc" / "pairable").exists() and exits == []
    assert (await p.factory_reset(3))["ok"] is False


async def test_reset_level_1_without_original_network_deletes_nothing(env):
    p, program, *_ = make(env)
    log = with_run(p, "friday-wifi-1700000000:802-11-wireless\nlo:loopback\n")
    r = await p.factory_reset(1)
    assert r == {"ok": False, "error": "no_original_network"}
    assert all(c[0] == "nmcli" for c in log) and program.calls == []


async def test_reset_level_1_reports_a_failed_reconnect(env):
    p, *_ = make(env)
    with_run(p, LISTING, up_code=4)
    assert (await p.factory_reset(1)) == {"ok": False, "error": "wifi_up"}


def test_wifi_profiles_parsing_handles_escaped_colons():
    own, original = pv.wifi_profiles("Casa\\:5G:802-11-wireless\nfriday-wifi-5:802-11-wireless\n")
    assert own == ["friday-wifi-5"] and original == "Casa:5G"
    assert pv.wifi_profiles("friday-wifi-x:802-11-wireless\n") == ([], "friday-wifi-x")


async def test_reset_home_press_needs_a_second_press_within_ten_seconds(env):
    clock = [100.0]
    p, program, exits, _ = make(env)
    p._monotonic = lambda: clock[0]
    r = await p.press_reset_home()
    assert r["ok"] and program.calls == [{"acao": "play_phrases", "keys": ["reset_confirm_ha"]}]
    assert not (env / "etc" / "pairable").exists()
    clock[0] += 9.5
    assert (await p.press_reset_home()) == {"ok": True}
    assert (env / "etc" / "pairable").exists()
    assert program.calls[-1] == {"acao": "play_phrases", "keys": ["reset_home"]}
    await p._exit_task


async def test_reset_home_second_press_after_the_window_only_rearms(env):
    clock = [100.0]
    p, program, *_ = make(env)
    p._monotonic = lambda: clock[0]
    await p.press_reset_home()
    clock[0] += 10.5
    await p.press_reset_home()
    assert not (env / "etc" / "pairable").exists()
    assert [c["keys"] for c in program.calls] == [["reset_confirm_ha"], ["reset_confirm_ha"]]
    clock[0] += 5
    await p.press_reset_home()  # inside the new window
    assert (env / "etc" / "pairable").exists()
    await p._exit_task


async def test_update_speaks_updating_and_cancels_the_slow_notice(env):
    blob = tar_bytes(UPDATE)
    p, program, *_ = make(env, {"http://vm/b": blob})
    never = asyncio.Event()
    waits = []

    async def sleep(seconds):
        waits.append(seconds)
        if seconds == pv.UPDATE_SLOW_S:
            await never.wait()  # the 180 s timer never gets to expire

    p._sleep = sleep
    r = await p.apply_bundle("update", "http://vm/b", sha(blob))
    assert r["ok"]
    await asyncio.sleep(0)
    assert [c["keys"] for c in program.calls] == [["updating"]]
    assert pv.UPDATE_SLOW_S in waits
    p._exit_task.cancel()


async def test_update_slow_is_spoken_once_when_the_update_takes_too_long(env):
    blob = tar_bytes(UPDATE)
    p, program, *_ = make(env, {"http://vm/b": blob})
    release = asyncio.Event()

    async def sleep(seconds):
        if seconds == pv.UPDATE_SLOW_S:
            release.set()  # the deadline passes at once...
            return None
        await asyncio.sleep(0)

    p._sleep = sleep
    orig = p._apply_update

    async def late(parts, stamp):  # ...while the update is still working
        await release.wait()
        for _ in range(5):
            await asyncio.sleep(0)
        return await orig(parts, stamp)

    p._apply_update = late
    r = await p.apply_bundle("update", "http://vm/b", sha(blob))
    assert r["ok"]
    assert [c["keys"] for c in program.calls] == [["updating"], ["update_slow"]]
    p._exit_task.cancel()


async def test_voices_bundle_is_silent(env):
    blob = tar_bytes(VOICES)
    p, program, *_ = make(env, {"http://vm/v": blob})
    await p.apply_bundle("voices", "http://vm/v", sha(blob))
    assert program.calls == []


async def test_reset_does_not_depend_on_the_program(env):
    class Down(FakeProgram):
        async def command(self, body):
            raise OSError("down")

    p, *_ = make(env, program=Down())
    assert (await p.factory_reset(2)) == {"ok": True}
    assert (env / "etc" / "pairable").exists()
    await p._exit_task


def test_descriptors_and_onboarding_event():
    keys = {c["key"]: c for c in cap.controls_from_config({}, [])}
    assert keys["reset_network"]["platform"] == "button" and keys["reset_home"]["platform"] == "button"
    assert keys["reset_network"]["category"] == "config"
    assert keys["onboarding"]["platform"] == "event"
    t = cap.Tracker()
    ev = t.on_event("onboarding", {"resultado": "area_selected", "area_id": "sala"})
    assert ev == {"event": ("onboarding", "area_selected", {"area_id": "sala"})}
    assert t.on_event("onboarding", {"resultado": "wake_word_ok"})["event"] == ("onboarding", "wake_word_ok", {})
    assert t.on_event("onboarding", {"resultado": "xyz"}) is None


async def test_default_fetch_downloads_and_hashes(aiohttp_server, tmp_path):
    blob = b"hello bundle"

    async def get(request):
        return web.Response(body=blob)

    app = web.Application()
    app.router.add_get("/b", get)
    server = await aiohttp_server(app)
    got = await pv.fetch_to_file(f"http://{server.host}:{server.port}/b", str(tmp_path / "f"))
    assert got == sha(blob) and (tmp_path / "f").read_bytes() == blob
    with pytest.raises(pv.BundleError):
        await pv.fetch_to_file(f"http://{server.host}:{server.port}/missing", str(tmp_path / "g"))


def _run_helper(src, dest, bundles):
    env = dict(os.environ, FRIDAY_AGENT_DEST=str(dest), FRIDAY_AGENT_BUNDLES=str(bundles))
    return subprocess.run(["sh", str(HELPER), str(src)], env=env, capture_output=True, text=True)


def test_agent_apply_helper_installs_and_refuses_outside_staging(tmp_path):
    bundles, dest = tmp_path / "b", tmp_path / "opt"
    stage = bundles / "stage-abc" / "agent"
    (stage / "friday_satellite_agent").mkdir(parents=True)
    (stage / "friday_agent_core").mkdir()
    (stage / "friday_satellite_agent" / "__main__.py").write_text("NEW")
    (stage / "friday_agent_core" / "__init__.py").write_text("CORE")
    (dest / "friday_satellite_agent").mkdir(parents=True)
    (dest / "friday_satellite_agent" / "old.py").write_text("OLD")
    (stage / "friday_satellite_agent" / "__main__.py").chmod(0o600)  # 06/11: a 0600 file kept the agent from starting
    r = _run_helper(stage, dest, bundles)
    assert r.returncode == 0, r.stderr
    assert (dest / "friday_satellite_agent" / "__main__.py").read_text() == "NEW"
    assert (dest / "friday_satellite_agent" / "__main__.py").stat().st_mode & 0o777 == 0o644
    assert not (dest / "friday_satellite_agent" / "old.py").exists()
    assert (dest / "friday_agent_core" / "__init__.py").read_text() == "CORE"
    other = tmp_path / "elsewhere" / "agent"
    other.mkdir(parents=True)
    assert _run_helper(other, dest, bundles).returncode == 2
    bad = bundles / "stage-bad" / "agent"
    bad.mkdir(parents=True)
    assert _run_helper(bad, dest, bundles).returncode == 3
