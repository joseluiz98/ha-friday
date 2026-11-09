"""SPAKE2 pairing on the satellite agent (the HA side is simulated here with SPAKE2_A + AES-GCM)."""

import base64
import hashlib
import hmac
import json
import sys
from pathlib import Path

import pytest
from aiohttp import web
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from spake2 import SPAKE2_A

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "agents" / "satellite"))
from friday_satellite_agent import pairing as pr  # noqa: E402


def _mac(key, label, a, b):
    return hmac.new(key, label + a + b, hashlib.sha256).digest()


class Ha:
    """The HA side of the exchange."""

    def __init__(self, code, token="tok-123"):
        self.token = token
        self.spake = SPAKE2_A(code.encode())
        self.msg_a = self.spake.start()

    def finish(self, msg_b, nonce=b"n" * 12):
        self.key = self.spake.finish(msg_b)
        self.msg_b = msg_b
        return (_mac(self.key, b"friday-pair-ha", self.msg_a, msg_b), nonce,
                AESGCM(self.key).encrypt(nonce, self.token.encode(), b"friday-pair"))


def test_full_exchange_with_right_code():
    s = pr.PairingSession(code="4821")
    ha = Ha("4821")
    msg_b = s.start(ha.msg_a)
    confirm, nonce, ct = ha.finish(msg_b)
    reply, token = s.finish(confirm, nonce, ct)
    assert token == "tok-123"
    assert reply == _mac(ha.key, b"friday-pair-sat", ha.msg_a, ha.msg_b)  # HA can verify the satellite


def test_wrong_code_is_bad_code_with_attempts_left():
    s = pr.PairingSession(code="4821")
    ha = Ha("0000")
    confirm, nonce, ct = ha.finish(s.start(ha.msg_a))
    with pytest.raises(pr.BadCode) as e:
        s.finish(confirm, nonce, ct)
    assert e.value.attempts_left == 4


def test_five_errors_lock_and_draw_a_new_code():
    s = pr.PairingSession(code="4821")
    old = s.code
    for i in range(4):
        ha = Ha("0000")
        confirm, nonce, ct = ha.finish(s.start(ha.msg_a))
        with pytest.raises(pr.BadCode) as e:
            s.finish(confirm, nonce, ct)
        assert e.value.attempts_left == 4 - i
    ha = Ha("0000")
    confirm, nonce, ct = ha.finish(s.start(ha.msg_a))
    with pytest.raises(pr.Locked):
        s.finish(confirm, nonce, ct)
    assert s.code != old and len(s.code) == 4 and s.attempts == 0
    # the old code no longer works: a fresh exchange with it fails
    ha = Ha(old)
    confirm, nonce, ct = ha.finish(s.start(ha.msg_a))
    with pytest.raises(pr.BadCode):
        s.finish(confirm, nonce, ct)


def test_new_start_invalidates_previous_session():
    s = pr.PairingSession(code="4821")
    first = Ha("4821")
    msg_b1 = s.start(first.msg_a)
    second = Ha("4821")
    s.start(second.msg_a)
    confirm, nonce, ct = first.finish(msg_b1)
    with pytest.raises(pr.BadCode):  # keyed to the first transcript: no longer matches
        s.finish(confirm, nonce, ct)


def test_finish_without_start():
    with pytest.raises(pr.BadPayload):
        pr.PairingSession(code="1234").finish(b"x", b"n" * 12, b"y")


def test_session_is_one_shot_after_success():
    s = pr.PairingSession(code="4821")
    ha = Ha("4821")
    confirm, nonce, ct = ha.finish(s.start(ha.msg_a))
    s.finish(confirm, nonce, ct)
    with pytest.raises(pr.BadPayload):
        s.finish(confirm, nonce, ct)


def test_code_is_four_digits_from_injected_rng():
    class Rng:
        def __init__(self):
            self.values = iter([7, 7, 4821])

        def randbelow(self, n):
            return next(self.values)

    s = pr.PairingSession(rng=Rng())
    assert s.code == "0007"
    assert s.new_code() == "4821"  # a repeat of the previous code is redrawn


def test_pairing_state_and_agent_token_three_scenarios(tmp_path):
    cfg = {"senha_porta_local": "legacy-pw"}
    # legacy: neither file
    assert pr.pairing_state(str(tmp_path)) == "paired"
    assert pr.agent_token(str(tmp_path), cfg) == "legacy-pw"
    # pairable
    (tmp_path / "pairable").write_text("")
    assert pr.pairing_state(str(tmp_path)) == "pairable"
    assert pr.agent_token(str(tmp_path), cfg) == "legacy-pw"
    # paired with ha_token
    (tmp_path / "pairable").unlink()
    (tmp_path / "ha_token").write_text("issued-by-ha\n")
    assert pr.pairing_state(str(tmp_path)) == "paired"
    assert pr.agent_token(str(tmp_path), cfg) == "issued-by-ha"


def test_persist_pairing_writes_600_and_removes_flag(tmp_path):
    (tmp_path / "pairable").write_text("")
    pr.persist_pairing(str(tmp_path), "secret-token")
    assert (tmp_path / "ha_token").read_text() == "secret-token"
    assert (tmp_path / "ha_token").stat().st_mode & 0o777 == 0o600
    assert not (tmp_path / "pairable").exists()


def _b(x):
    return base64.b64encode(x).decode()


async def test_routes_exchange_over_http(aiohttp_client, tmp_path):
    got = []

    async def on_paired(token):
        got.append(token)

    app = web.Application()
    s = pr.PairingSession(code="4821")
    pr.add_routes(app, s, "sat-abc123", on_paired)
    client = await aiohttp_client(app)
    ha = Ha("4821")
    r = await client.post("/v1/pair/start", json={"msg": _b(ha.msg_a)})
    assert r.status == 200
    msg_b = base64.b64decode((await r.json())["msg"])
    confirm, nonce, ct = ha.finish(msg_b)
    r = await client.post("/v1/pair/finish", json={"confirm": _b(confirm), "nonce": _b(nonce), "token": _b(ct)})
    assert r.status == 200
    body = await r.json()
    assert body["id"] == "sat-abc123"
    assert base64.b64decode(body["confirm"]) == _mac(ha.key, b"friday-pair-sat", ha.msg_a, ha.msg_b)
    assert got == ["tok-123"]


async def test_routes_wrong_code_403_then_locked(aiohttp_client):
    async def on_paired(token):
        raise AssertionError("must not pair")

    app = web.Application()
    s = pr.PairingSession(code="4821", max_attempts=2)
    pr.add_routes(app, s, "sat-abc123", on_paired)
    client = await aiohttp_client(app)

    async def attempt():
        ha = Ha("0000")
        r = await client.post("/v1/pair/start", json={"msg": _b(ha.msg_a)})
        confirm, nonce, ct = ha.finish(base64.b64decode((await r.json())["msg"]))
        return await client.post("/v1/pair/finish",
                                 json={"confirm": _b(confirm), "nonce": _b(nonce), "token": _b(ct)})

    r = await attempt()
    assert r.status == 403 and await r.json() == {"error": "bad_code", "attempts_left": 1}
    r = await attempt()
    assert r.status == 403 and await r.json() == {"error": "locked"}


async def test_routes_reject_garbage(aiohttp_client):
    app = web.Application()
    pr.add_routes(app, pr.PairingSession(code="1111"), "sat-1", lambda t: None)
    client = await aiohttp_client(app)
    assert (await client.post("/v1/pair/start", json={"msg": "###"})).status == 400
    assert (await client.post("/v1/pair/start", json=[1])).status == 400
    assert (await client.post("/v1/pair/finish", json={"confirm": "", "nonce": "", "token": ""})).status == 400


async def test_pairing_routes_absent_when_paired(aiohttp_client):
    client = await aiohttp_client(web.Application())  # an agent that is `paired` never registers them
    assert (await client.post("/v1/pair/start", json={"msg": "AA=="})).status == 404
    assert (await client.post("/v1/pair/finish", json={})).status == 404
