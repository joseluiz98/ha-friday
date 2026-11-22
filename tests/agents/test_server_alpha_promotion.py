"""The Alpha promotion switch and command of the server agent (plan 2026-11-22 Task 2)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "agents" / "server"))

from friday_server_agent import __main__ as m  # noqa: E402


class FakeOrch:
    def __init__(self, allowed=True, tracked=("v1.1.0", "soaking")):
        self.allowed, self.calls, self.tracked = allowed, [], tracked

    def image_status(self):
        return {}

    def server_entity(self):
        return {"installed": "v1.0.0"}

    def status(self):
        return {"server": {}}

    def waiting_server(self):
        return {}

    def alpha_promotion(self):
        return None, {"tag": self.tracked[0], "status": self.tracked[1], "alphas": []}

    def promotion_allowed(self):
        return self.allowed

    def set_promotion(self, tag, allowed):
        self.calls.append((tag, allowed))
        if self.allowed is None:
            return {"ok": False, "error": "no_alpha"}
        self.allowed = allowed
        return {"ok": True, "tag": tag or "v1.1.0"}


class Spy:
    def __init__(self):
        self.pushed, self.logs, self.state_pushes = [], [], 0

    async def push_log(self, message, data):
        self.logs.append((message, data))

    async def ota_state_push(self):
        self.state_pushes += 1


def _dispatch(orch):
    spy = Spy()

    async def other(cmd):
        return {"ok": True, "other": cmd.get("key")}

    return m.make_set_dispatch(orch, {}, lambda *a: None, spy.ota_state_push, spy.push_log, other), spy


def test_ota_entities_has_allowed():
    assert m.ota_entities(FakeOrch(allowed=False))["ota_alpha_promotion_allowed"] is False
    assert m.ota_entities(FakeOrch(allowed=True))["ota_alpha_promotion_allowed"] is True


def test_switch_value_none_when_failed():
    # the value comes from the orchestrator: None for a failed tracked alpha -> `unavailable`, never True
    assert m.ota_entities(FakeOrch(allowed=None))["ota_alpha_promotion_allowed"] is None


async def test_set_dispatch_alpha_promotion():
    orch = FakeOrch()
    dispatch, spy = _dispatch(orch)
    assert await dispatch({"key": "ota_alpha_promotion_allowed", "value": False}) == {"ok": True}
    assert orch.calls == [(None, False)]
    assert spy.state_pushes == 1
    assert spy.logs == [("Alpha promotion of v1.1.0 blocked by the owner", {"tag": "v1.1.0"})]
    assert await dispatch({"key": "ota_alpha_promotion_allowed", "value": "off"}) == {"ok": False, "error": "value"}
    assert orch.calls == [(None, False)]  # the bad value never reached the orchestrator
    assert await dispatch({"key": "ota_alpha_promotion_allowed", "value": True}) == {"ok": True}
    assert spy.logs[-1] == ("Alpha promotion of v1.1.0 allowed by the owner", {"tag": "v1.1.0"})
    assert await dispatch({"key": "something_else", "value": 1}) == {"ok": True, "other": "something_else"}


async def test_set_dispatch_alpha_promotion_error_is_returned():
    dispatch, spy = _dispatch(FakeOrch(allowed=None))
    assert await dispatch({"key": "ota_alpha_promotion_allowed", "value": True}) == {"ok": False, "error": "no_alpha"}
    assert spy.logs == [] and spy.state_pushes == 0


async def test_alpha_promotion_command_passes_tag():
    orch = FakeOrch()
    spy = Spy()
    handler = m.make_alpha_promotion_handler(orch, spy.ota_state_push, spy.push_log)
    assert await handler({"cmd": "alpha_promotion", "tag": "v1.2.0", "allowed": False}) == {"ok": True, "tag": "v1.2.0"}
    assert orch.calls == [("v1.2.0", False)]
    assert spy.logs == [("Alpha promotion of v1.2.0 blocked by the owner", {"tag": "v1.2.0"})]
    assert await handler({"cmd": "alpha_promotion", "allowed": True}) == {"ok": True, "tag": "v1.1.0"}
    assert orch.calls[-1] == (None, True)
    assert await handler({"cmd": "alpha_promotion", "allowed": 1}) == {"ok": False, "error": "value"}
    assert await handler({"cmd": "alpha_promotion", "tag": 5, "allowed": True}) == {"ok": False, "error": "unknown_tag"}
