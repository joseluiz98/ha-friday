"""LED ring preview button (15/11)."""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "agents" / "satellite"))
from friday_satellite_agent import ring_preview as rp  # noqa: E402


class Fake:
    def __init__(self, states=None):
        self.sent, self.states = [], list(states or [])

    async def command(self, body):
        self.sent.append(body)
        return {}

    def state(self):
        return self.states.pop(0) if len(self.states) > 1 else (self.states[0] if self.states else "idle")

    async def sleep(self, _s):
        pass


def test_shows_every_state_then_the_countdown_and_ends_off():
    f = Fake()
    assert asyncio.run(rp.RingPreview(f.command, f.state, f.sleep).run()) == "done"
    shown = [c["state"] for c in f.sent if c["acao"] == "ring"]
    assert shown == list(rp.SEQUENCE) + [None]  # v0.3.7: None ends the preview: the ring goes back to the composition
    assert all(c.get("preview") is True for c in f.sent if c["acao"] == "ring")
    assert {"acao": "ring_countdown", "seconds": 10} in f.sent


def test_every_state_is_one_the_program_accepts():
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "satellite" / "program"))
    import ring
    assert set(rp.SEQUENCE) <= set(ring.ESTADOS)


def test_a_conversation_ends_the_preview_without_touching_the_ring_again():
    f = Fake(["idle", "idle", "conversation"])
    assert asyncio.run(rp.RingPreview(f.command, f.state, f.sleep).run()) == "interrupted"
    assert [c["state"] for c in f.sent] == ["off", "update_pending"]  # interrupted: no end command


def test_refused_when_not_idle_or_already_running():
    async def go():
        f = Fake(["listening"])
        assert rp.RingPreview(f.command, f.state, f.sleep).start() == {"ok": False, "error": "not_idle"}
        g = Fake()
        p = rp.RingPreview(g.command, g.state, asyncio.sleep)
        assert p.start() == {"ok": True}
        assert p.start() == {"ok": False, "error": "busy"}
        p._task.cancel()
    asyncio.run(go())


def test_a_program_error_still_ends_off():
    class Boom(Fake):
        async def command(self, body):
            self.sent.append(body)
            if body.get("state") == "think":
                raise OSError("socket")
            return {}
    f = Boom()
    assert asyncio.run(rp.RingPreview(f.command, f.state, f.sleep).run()) == "error"
    assert f.sent[-1] == {"acao": "ring", "state": None, "preview": True}
