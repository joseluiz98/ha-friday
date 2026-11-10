"""Satellite voice switch (with courtesy), reboot and hidden restart_program. Never runs real commands."""

import sys
from pathlib import Path

SAT = Path(__file__).resolve().parents[2] / "agents" / "satellite"
sys.path.insert(0, str(SAT))

from friday_satellite_agent import capabilities as cap
from friday_satellite_agent import power as pw


class Runner:
    def __init__(self):
        self.calls = []

    async def __call__(self, argv):
        self.calls.append(list(argv))
        return 0


def make(tmp_path, state="idle", **kw):
    runner = Runner()
    box = {"s": state}
    sleeps = []

    async def sleep(s):
        sleeps.append(s)
        if "script" in box and box["script"]:
            box["s"] = box["script"].pop(0)

    p = pw.Power(runner, str(tmp_path / "voice-enabled"), lambda: box["s"], sleep=sleep, **kw)
    return p, runner, box, sleeps


async def test_off_waits_for_conversation_end(tmp_path):
    p, runner, box, sleeps = make(tmp_path, state="conversation")
    box["script"] = ["conversation", "conversation", "idle"]
    await p.set_voice(False)
    assert sleeps == [1, 1, 1]
    assert runner.calls == [pw.STOP]
    assert open(p.path).read().strip() == "off"


async def test_off_stops_anyway_after_120s(tmp_path):
    p, runner, box, sleeps = make(tmp_path, state="conversation")
    await p.set_voice(False)
    assert len(sleeps) == 120 and runner.calls == [pw.STOP]


async def test_on_starts_and_persists(tmp_path):
    p, runner, _, _ = make(tmp_path)
    await p.set_voice(False)
    await p.set_voice(True)
    assert runner.calls[-1] == pw.START
    assert open(p.path).read().strip() == "on"


async def test_off_persists_across_agent_restart(tmp_path):
    p, _, _, _ = make(tmp_path)
    await p.set_voice(False)
    p2, runner2, _, _ = make(tmp_path)
    assert p2.enabled is False
    await p2.startup()
    assert runner2.calls == [pw.STOP]
    (tmp_path / "other").mkdir()
    p3, runner3, _, _ = make(tmp_path / "other")
    await p3.startup()
    assert p3.enabled is True and runner3.calls == []


async def test_reboot_refused_in_conversation(tmp_path):
    p, runner, _, _ = make(tmp_path, state="conversation")
    assert await p.reboot() == {"ok": False, "error": "conversation_open"}
    assert runner.calls == []


async def test_reboot_runs_when_idle_and_logs(tmp_path):
    logs = []

    async def log(msg):
        logs.append(msg)

    p, runner, _, _ = make(tmp_path, log=log)
    assert (await p.reboot())["ok"] is True
    assert runner.calls == [pw.REBOOT] and logs


async def test_restart_program(tmp_path):
    p, runner, _, _ = make(tmp_path)
    assert (await p.restart_program())["ok"] is True
    assert runner.calls == [pw.RESTART]


def test_argv_are_exact_sudoers_commands():
    assert pw.STOP == ["sudo", "-n", "/usr/bin/systemctl", "stop", "friday-satelite"]
    assert pw.REBOOT == ["sudo", "-n", "/usr/sbin/reboot"]


def test_descriptors():
    caps = {d["key"]: d for d in cap.controls_from_config({}, [])}
    assert caps["voice_enabled"]["platform"] == "switch" and caps["voice_enabled"]["name"] == "Assistant"
    assert caps["reboot"]["platform"] == "button" and caps["reboot"]["device_class"] == "restart"
    r = caps["restart_program"]
    assert r["platform"] == "button" and r["hidden"] is True and r["available"] is False


def test_sudoers_file_is_exact():
    text = (SAT / "sudoers.friday-satellite-agent").read_text()
    rules = [ln for ln in text.splitlines() if ln.strip() and not ln.startswith("#")]
    assert len(rules) == 1 and rules[0].startswith("jose ALL=(root) NOPASSWD: ")
    cmds = [c.strip() for c in rules[0].split("NOPASSWD:")[1].split(",")]
    assert cmds == ["/usr/bin/systemctl start friday-satelite", "/usr/bin/systemctl stop friday-satelite",
                    "/usr/bin/systemctl restart friday-satelite", "/usr/sbin/reboot", "/usr/local/sbin/friday-wifi-apply",
                    "/usr/local/sbin/friday-agent-apply"]


async def test_startup_stop_failure_does_not_raise(tmp_path):
    p, runner, _, _ = make(tmp_path)
    p.persist(False)

    async def boom(argv):
        raise OSError("x")

    p._run = boom
    await p.startup()


async def test_reboot_log_failure_does_not_block(tmp_path):
    async def bad(msg):
        raise RuntimeError("x")

    p, runner, _, _ = make(tmp_path, log=bad)
    assert (await p.reboot())["ok"] is True and runner.calls == [pw.REBOOT]


async def test_nonzero_exit_is_command_failed(tmp_path):
    p, runner, _, _ = make(tmp_path)

    async def fail(argv):
        return 1

    p._run = fail
    assert await p.reboot() == {"ok": False, "error": "command_failed"}
    assert await p.restart_program() == {"ok": False, "error": "command_failed"}
