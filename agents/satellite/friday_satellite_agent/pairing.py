"""SPAKE2 pairing while the satellite is `pairable` (spec 2026-11-05 §6.3, §9).

The satellite speaks a 4-digit code; whoever types it into Home Assistant proves presence and receives nothing
until the exchange succeeds. HA is SPAKE2_A, the satellite SPAKE2_B; K (32 bytes) keys the two confirmations and
the AES-GCM that carries the token HA generated. Wire format: see the plan's shared-interfaces table.
"""

from __future__ import annotations

import base64
import binascii
import hmac
import hashlib
import logging
import os
import secrets
from typing import Awaitable, Callable

from aiohttp import web
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from spake2 import SPAKE2_B

LOG = logging.getLogger("friday_satellite_agent")
PAIRABLE = "pairable"
HA_TOKEN = "ha_token"
CONFIRM_HA = b"friday-pair-ha"
CONFIRM_SAT = b"friday-pair-sat"
AAD = b"friday-pair"


class BadCode(Exception):
    def __init__(self, attempts_left: int) -> None:
        super().__init__("bad_code")
        self.attempts_left = attempts_left


class Locked(Exception):
    pass


class BadPayload(Exception):
    pass


def pairing_state(etc_dir: str) -> str:
    return "pairable" if os.path.exists(os.path.join(etc_dir, PAIRABLE)) else "paired"


def agent_token(etc_dir: str, cfg: dict) -> str:
    """Token HA must present: the one HA issued, else the legacy local password (satellites paired by hand)."""
    try:
        with open(os.path.join(etc_dir, HA_TOKEN), encoding="utf-8") as f:
            token = f.read().strip()
        if token:
            return token
    except OSError:
        pass
    return cfg["senha_porta_local"]


def _mac(key: bytes, label: bytes, msg_a: bytes, msg_b: bytes) -> bytes:
    return hmac.new(key, label + msg_a + msg_b, hashlib.sha256).digest()


class PairingSession:
    def __init__(self, code: str | None = None, max_attempts: int = 5, rng=secrets) -> None:
        self._rng = rng
        self._max = max_attempts
        self.code = code or self._draw()
        self.attempts = 0
        self._spake = None
        self._key: bytes | None = None
        self._msgs: tuple[bytes, bytes] | None = None

    def _draw(self) -> str:
        return f"{self._rng.randbelow(10000):04d}"

    def new_code(self) -> str:
        old = self.code
        while True:
            self.code = self._draw()
            if self.code != old:
                break
        self.attempts = 0
        self._drop()
        return self.code

    def _drop(self) -> None:
        self._spake = self._key = self._msgs = None

    def start(self, msg_a: bytes) -> bytes:
        """New session each time: a fresh start invalidates the previous one."""
        self._drop()
        spake = SPAKE2_B(self.code.encode())
        msg_b = spake.start()
        try:
            self._key = spake.finish(msg_a)
        except Exception as err:  # malformed message from the peer
            self._drop()
            raise BadPayload("bad_message") from err
        self._msgs = (msg_a, msg_b)
        return msg_b

    def finish(self, confirm: bytes, nonce: bytes, token_ct: bytes) -> tuple[bytes, str]:
        if self._key is None or self._msgs is None:
            raise BadPayload("no_session")
        msg_a, msg_b = self._msgs
        if not hmac.compare_digest(confirm, _mac(self._key, CONFIRM_HA, msg_a, msg_b)):
            self.attempts += 1
            left = self._max - self.attempts
            if left <= 0:
                self.new_code()
                raise Locked()
            raise BadCode(left)
        try:
            token = AESGCM(self._key).decrypt(nonce, token_ct, AAD).decode()
        except (InvalidTag, ValueError, UnicodeDecodeError) as err:
            raise BadPayload("bad_token") from err
        if not token.strip():
            raise BadPayload("empty_token")
        reply = _mac(self._key, CONFIRM_SAT, msg_a, msg_b)
        self.attempts = 0
        self._drop()
        return reply, token


def _b64d(value) -> bytes:
    if not isinstance(value, str):
        raise BadPayload("not_base64")
    try:
        return base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as err:
        raise BadPayload("not_base64") from err


def _b64e(raw: bytes) -> str:
    return base64.b64encode(raw).decode()


def add_routes(app: web.Application, session: PairingSession, sat_id: str,
               on_paired: Callable[[str], Awaitable[None]]) -> None:
    """Registers /v1/pair/start and /v1/pair/finish. Call only while the satellite is `pairable`."""

    async def body(request: web.Request) -> dict:
        try:
            data = await request.json()
        except ValueError:
            raise web.HTTPBadRequest()
        if not isinstance(data, dict):
            raise web.HTTPBadRequest()
        return data

    async def start(request: web.Request) -> web.Response:
        data = await body(request)
        try:
            msg_b = session.start(_b64d(data.get("msg")))
        except BadPayload:
            return web.json_response({"error": "bad_request"}, status=400)
        return web.json_response({"msg": _b64e(msg_b)})

    async def finish(request: web.Request) -> web.Response:
        data = await body(request)
        try:
            confirm, token_ct = _b64d(data.get("confirm")), _b64d(data.get("token"))
            nonce = _b64d(data.get("nonce"))
            if len(nonce) != 12:
                raise BadPayload("nonce")
            reply, token = session.finish(confirm, nonce, token_ct)
        except BadCode as err:
            return web.json_response({"error": "bad_code", "attempts_left": err.attempts_left}, status=403)
        except Locked:
            return web.json_response({"error": "locked"}, status=403)
        except BadPayload:
            return web.json_response({"error": "bad_request"}, status=400)
        await on_paired(token)  # persists and schedules the restart; the reply still goes out first
        return web.json_response({"confirm": _b64e(reply), "id": sat_id})

    app.router.add_post("/v1/pair/start", start)
    app.router.add_post("/v1/pair/finish", finish)


def persist_pairing(etc_dir: str, token: str) -> None:
    """Token first (600, atomic), then the `pairable` flag goes away: a crash in between stays pairable."""
    path = os.path.join(etc_dir, HA_TOKEN)
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(token)
    os.replace(tmp, path)
    try:
        os.remove(os.path.join(etc_dir, PAIRABLE))
    except FileNotFoundError:
        pass
