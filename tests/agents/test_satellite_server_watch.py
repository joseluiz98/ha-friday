"""The satellite tells the room when the F.R.I.D.A.Y. server cannot be reached (phrase no_server)."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "agents" / "satellite"))
from friday_satellite_agent import server_watch as sw  # noqa: E402


class Program:
    def __init__(self):
        self.calls = []

    async def command(self, body):
        self.calls.append(body)
        return {"ok": True}


def cfg(tmp_path, url="https://192.0.2.251:7443"):
    p = tmp_path / "config.json"
    p.write_text(json.dumps({"endereco_friday": url}))
    return str(p)


def test_target_comes_from_endereco_friday(tmp_path):
    assert sw.server_target(cfg(tmp_path)) == ("192.0.2.251", 7443)
    assert sw.server_target(cfg(tmp_path, "https://vm.local")) == ("vm.local", 443)
    assert sw.server_target(str(tmp_path / "missing.json")) is None
    assert sw.server_target(cfg(tmp_path, "")) is None


async def test_three_failures_speak_once_then_rearm_after_a_success(tmp_path):
    up = [False]
    seen = []

    async def probe(host, port):
        seen.append((host, port))
        return up[0]

    program = Program()
    w = sw.ServerWatch(cfg(tmp_path), program, probe=probe)
    for _ in range(2):
        await w.step()
    assert program.calls == []
    for _ in range(4):
        await w.step()
    assert program.calls == [{"acao": "play_phrases", "keys": ["no_server"]}, {"acao": "ring", "state": "error"}]
    assert seen[0] == ("192.0.2.251", 7443)
    up[0] = True
    await w.step()
    assert w.fails == 0 and not w.told
    up[0] = False
    for _ in range(3):
        await w.step()
    assert len(program.calls) == 4  # spoken again for the new outage


async def test_a_refusing_program_does_not_break_the_loop(tmp_path):
    class Down(Program):
        async def command(self, body):
            raise OSError("down")

    async def probe(host, port):
        return False

    w = sw.ServerWatch(cfg(tmp_path), Down(), probe=probe)
    for _ in range(5):
        await w.step()
    assert w.told


async def test_tcp_reachable_refuses_a_closed_port():
    assert await sw.tcp_reachable("127.0.0.1", 1, timeout=1) is False
