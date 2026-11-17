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


def make_switch(tmp_path, program_state, *, intent_on=True):
    """A VoiceSwitch wired like main(): refresh derives voice_enabled from the program and publishes it."""
    from friday_satellite_agent import __main__ as m
    from friday_satellite_agent import power as pw

    prog = FakeProgram(program_state)
    memo = {"program_state": "off"}
    pushed = []
    runner_calls = []

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
        await refresh("agent")

    path = str(tmp_path / "voice-enabled")
    power = pw.Power(runner, path, lambda: memo["program_state"], sleep=sleep, show_runner=show)
    power.persist(intent_on)
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
    await asyncio.gather(*sw._off)
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
