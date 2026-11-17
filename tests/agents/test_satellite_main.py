"""Satellite agent startup guards."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "agents" / "satellite"))


@pytest.mark.parametrize("password", ["", "   "])
async def test_satellite_agent_refuses_empty_local_password(tmp_path, monkeypatch, password):
    from friday_satellite_agent import __main__ as m
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"senha_porta_local": password, "porta_local": 8700}))
    monkeypatch.setattr(m, "CONFIG", str(cfg))
    with pytest.raises(SystemExit):
        await m.main()


class FakeProgram:
    """Stands in for the voice program: `state` None = not answering (the refresh then reports it off)."""

    def __init__(self, state):
        self.state = state


def make_switch(tmp_path, program_state, *, intent_on=True, log=None):
    """A VoiceSwitch wired like main(): refresh derives voice_enabled from the program and publishes it."""
    from friday_satellite_agent import __main__ as m
    from friday_satellite_agent import power as pw

    prog = FakeProgram(program_state)
    memo = {"program_state": "off"}
    pushed = []
    runner_calls = []
    sleeps = []

    async def runner(argv):
        runner_calls.append(list(argv))
        return 0

    async def show(argv):
        return "ActiveState=inactive\nSubState=dead\nResult=exec-condition\n"

    async def refresh(origin):
        changes = {"program_state": prog.state or "off"}
        m.VoiceSwitch.derive(changes)
        memo.update(changes)
        pushed.append((origin, dict(changes)))

    async def sleep(_s):
        sleeps.append(_s)
        await refresh("agent")

    path = str(tmp_path / "voice-enabled")
    power = pw.Power(runner, path, lambda: memo["program_state"], sleep=sleep, show_runner=show, log=log)
    power.persist(intent_on)
    power.sleeps = sleeps
    return m.VoiceSwitch(power, refresh), power, prog, memo, pushed, runner_calls, refresh


async def test_on_when_program_runs(tmp_path):
    sw, power, prog, memo, *_ , refresh = make_switch(tmp_path, "idle")
    await refresh("agent")
    assert memo["voice_enabled"] is True


async def test_off_when_stopped_even_with_intent_on(tmp_path):
    sw, power, prog, memo, *_, refresh = make_switch(tmp_path, None, intent_on=True)
    assert power.enabled is True
    await refresh("agent")
    assert memo["voice_enabled"] is False


async def test_off_shows_real_state_during_courtesy_wait(tmp_path):
    import asyncio
    from friday_satellite_agent import power as pw

    sw, power, prog, memo, pushed, calls, refresh = make_switch(tmp_path, "conversation")
    await refresh("agent")
    assert memo["voice_enabled"] is True
    assert await sw.set(False) == {"ok": True}
    assert open(power.path).read().strip() == "off"  # durable at once
    assert memo["voice_enabled"] is True  # still really running: the courtesy wait
    prog.state = None  # the conversation ended and the program stopped
    await asyncio.gather(*sw._tasks)
    assert calls == [pw.STOP] and memo["voice_enabled"] is False


async def test_set_on_publishes_what_is_true_not_what_was_asked(tmp_path):
    sw, power, prog, memo, pushed, calls, refresh = make_switch(tmp_path, None, intent_on=False)
    await refresh("agent")
    assert await sw.set(True) == {"ok": True}  # the program was refused by the start condition
    assert memo["voice_enabled"] is False and open(power.path).read().strip() == "on"
    prog.state = "idle"
    assert await sw.set(True) == {"ok": True} and memo["voice_enabled"] is True


async def test_agent_restart_derives_from_program_not_file(tmp_path):
    from friday_satellite_agent import power as pw

    sw, power, prog, memo, pushed, calls, refresh = make_switch(tmp_path, None, intent_on=True)
    await refresh("agent")
    assert memo["voice_enabled"] is False  # file says on, program is stopped
    power.persist(False)
    await power.startup()
    assert calls == [pw.STOP]


async def test_turning_on_replies_before_the_start_wait(tmp_path):
    """HA gives a command 10 s and the agent handles commands one at a time: the 30 s wait must not block the reply."""
    from friday_satellite_agent import power as pw

    logs = []

    async def log(msg):
        logs.append(msg)

    sw, power, prog, memo, pushed, calls, refresh = make_switch(tmp_path, None, intent_on=False, log=log)
    assert await sw.set(True) == {"ok": True}
    assert calls == [pw.START]
    assert power.sleeps == []  # replied without waiting for the program
    assert logs == []
    import asyncio
    await asyncio.gather(*sw._tasks)  # the background wait still runs, and reports the failure
    assert len(power.sleeps) == pw.START_WAIT_S
    assert logs == ["Voice program did not start: voice program skipped: start condition not met "
                    "(no supported audio device)"]
    assert memo["voice_enabled"] is False


async def test_turning_on_background_wait_ends_when_program_comes_up(tmp_path):
    import asyncio

    logs = []

    async def log(msg):
        logs.append(msg)

    sw, power, prog, memo, pushed, calls, refresh = make_switch(tmp_path, None, intent_on=False, log=log)
    assert await sw.set(True) == {"ok": True}
    prog.state = "idle"
    await asyncio.gather(*sw._tasks)
    assert memo["voice_enabled"] is True and logs == [] and len(power.sleeps) == 1


async def test_start_failure_is_reported_at_once(tmp_path):
    sw, power, prog, memo, pushed, calls, refresh = make_switch(tmp_path, None, intent_on=False)

    async def fail(argv):
        return 1

    power._run = fail
    assert await sw.set(True) == {"ok": False, "error": "command_failed"}
    assert sw._tasks == []


async def test_a_newer_command_replaces_the_pending_start_wait(tmp_path):
    import asyncio

    sw, power, prog, memo, pushed, calls, refresh = make_switch(tmp_path, None, intent_on=False)
    assert await sw.set(True) == {"ok": True}
    first = sw._tasks[0]
    assert await sw.set(False) == {"ok": True}
    await asyncio.sleep(0)
    assert first.cancelled() or first.done()
    await asyncio.gather(*sw._tasks)


class ProgramStub:
    """The program's local API as the agent sees it: `up` False = nothing answers (status None)."""

    def __init__(self):
        self.up = True
        self.cfg = {"mudo": True, "nao_perturbe": True, "sons_turno": False, "music_duck_percent": 30,
                    "whisper_min_ratio_db": -20, "continuacao_s": 8, "volume": 3}
        self.ww = [{"nome": "friday", "modo": "ativa", "limiar": 0.5}]

    async def status(self):
        return {"estado": "ocioso", "mudo": False} if self.up else None

    async def get_config(self):
        return self.cfg

    async def wakewords(self):
        return self.ww


async def test_program_down_makes_every_program_derived_key_unavailable():
    """Owner rule: a switch shows the real state; with the program stopped there is none, so None (unavailable)."""
    from friday_satellite_agent import __main__ as m
    from friday_satellite_agent import capabilities as cap

    prog, mapper = ProgramStub(), cap.Mapper()
    memo = {"cfg": None, "ww": [], "state": {"program_state": "off", "sw_version": "x"}}

    up = await m.read_program(prog, mapper, memo)
    assert up["mute"] is True and up["do_not_disturb"] is True and up["wakeword_friday_mode"] is not None
    memo["state"].update(up)

    prog.up = False
    down = await m.read_program(prog, mapper, memo)
    derived = {k for k in up if k != "program_state"}
    assert {"mute", "do_not_disturb", "ducking", "whisper_mode", "follow_up_mode", "turn_sounds", "volume",
            "wakeword_friday_mode"} <= derived
    assert down["program_state"] == "off"
    assert all(down[k] is None for k in derived), {k: down.get(k) for k in derived}
    assert memo["cfg"] is None

    prog.up = True
    again = await m.read_program(prog, mapper, memo)
    assert again["mute"] is True and again["turn_sounds"] is False  # the real values are back


async def test_program_down_from_the_start_publishes_only_program_state():
    from friday_satellite_agent import __main__ as m
    from friday_satellite_agent import capabilities as cap

    prog = ProgramStub()
    prog.up = False
    memo = {"cfg": None, "ww": [], "state": {}}
    assert await m.read_program(prog, cap.Mapper(), memo) == {"program_state": "off"}
