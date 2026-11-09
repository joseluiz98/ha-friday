"""Adopt a pairable satellite: SPAKE2 exchange keyed by the code the satellite speaks (spec 2026-11-05 §6.3, §9).

HA is SPAKE2_A. `K` (32 bytes) keys the two HMAC confirmations and the AES-GCM that carries the token HA generated.
Wire format: the plan's shared-interfaces table; the agent side is agents/satellite/friday_satellite_agent/pairing.py.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import os
import secrets

import aiohttp
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from spake2 import SPAKE2_A

CONFIRM_HA = b"friday-pair-ha"
CONFIRM_SAT = b"friday-pair-sat"
AAD = b"friday-pair"
TIMEOUT_S = 10


class PairingError(Exception):
    """Base class."""


class BadCode(PairingError):
    def __init__(self, attempts_left: int) -> None:
        super().__init__("bad_code")
        self.attempts_left = attempts_left


class Locked(PairingError):
    """Too many wrong codes: the satellite spoke a new one."""


class CannotConnect(PairingError):
    """Unreachable, malformed answer, or a satellite that could not prove it knew the code."""


def _mac(key: bytes, label: bytes, msg_a: bytes, msg_b: bytes) -> bytes:
    return hmac.new(key, label + msg_a + msg_b, hashlib.sha256).digest()


def _b64e(raw: bytes) -> str:
    return base64.b64encode(raw).decode()


def _b64d(value) -> bytes:
    try:
        return base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError, TypeError) as err:
        raise CannotConnect("bad_response") from err


async def _post(session: aiohttp.ClientSession, url: str, body: dict) -> tuple[int, dict]:
    try:
        async with session.post(url, json=body, timeout=aiohttp.ClientTimeout(total=TIMEOUT_S)) as r:
            try:
                data = await r.json()
            except (aiohttp.ContentTypeError, ValueError):
                data = {}
            return r.status, data if isinstance(data, dict) else {}
    except (aiohttp.ClientError, TimeoutError, OSError) as err:
        raise CannotConnect("unreachable") from err


async def pair(session: aiohttp.ClientSession, host: str, port: int, code: str) -> tuple[str, str]:
    """Returns (token, sat_id); the token is generated here and only ever sent encrypted."""
    base = f"http://{host}:{port}"
    token = secrets.token_urlsafe(32)
    spake = SPAKE2_A(code.encode())
    msg_a = spake.start()
    status, data = await _post(session, f"{base}/v1/pair/start", {"msg": _b64e(msg_a)})
    if status != 200 or "msg" not in data:
        raise CannotConnect("start_failed")
    msg_b = _b64d(data["msg"])
    try:
        key = spake.finish(msg_b)
    except Exception as err:  # noqa: BLE001 - malformed peer message
        raise CannotConnect("bad_response") from err
    nonce = os.urandom(12)
    body = {"confirm": _b64e(_mac(key, CONFIRM_HA, msg_a, msg_b)), "nonce": _b64e(nonce),
            "token": _b64e(AESGCM(key).encrypt(nonce, token.encode(), AAD))}
    status, data = await _post(session, f"{base}/v1/pair/finish", body)
    if status == 403:
        if data.get("error") == "locked":
            raise Locked()
        left = data.get("attempts_left")
        raise BadCode(left if isinstance(left, int) else 0)
    if status != 200 or "confirm" not in data or not isinstance(data.get("id"), str):
        raise CannotConnect("finish_failed")
    if not hmac.compare_digest(_b64d(data["confirm"]), _mac(key, CONFIRM_SAT, msg_a, msg_b)):
        raise CannotConnect("satellite_confirm_mismatch")  # the satellite did not know the code
    return token, data["id"]
