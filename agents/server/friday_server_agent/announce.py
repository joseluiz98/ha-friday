"""Announce command: the integration asks, the bot (voice gateway) speaks on the satellites.

The agent only forwards `{"text", "satellites"}` to the bot's POST /announce with a bearer token that the
agent creates (600) on the first command and the bot reads from the same file (FRIDAY_TOKEN_ANNOUNCE). The token is never logged.
"""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import urllib.error
import urllib.request

BOT_URL = "http://127.0.0.1:7860/announce"
TOKEN_PATH = "/srv/friday/state/announce.token"  # default; FRIDAY_TOKEN_ANNOUNCE overrides it, as in the bot
TIMEOUT_S = 25  # the bot answers within its 18 s first-round budget (TTS + first sends); the integration waits 30 s


def token_path() -> str:
    return os.environ.get("FRIDAY_TOKEN_ANNOUNCE") or TOKEN_PATH


def token(path: str = TOKEN_PATH) -> str:
    """The bearer: read from `path`, or created there (mode 600) the first time."""
    try:
        with open(path, encoding="utf-8") as f:
            value = f.read().strip()
        if value:
            return value
    except FileNotFoundError:
        pass
    value = secrets.token_urlsafe(32)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(value + "\n")
    os.chmod(path, 0o600)
    return value


def http_post(url: str, bearer: str, body: dict) -> tuple[int, dict]:
    """(status, json). Raises OSError when the bot cannot be reached."""
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(), method="POST",
        headers={"Authorization": f"Bearer {bearer}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as err:
        try:
            return err.code, json.load(err)
        except ValueError:
            return err.code, {}
    except urllib.error.URLError as err:
        raise OSError(type(err.reason).__name__) from err


def make_handlers(post=http_post, path: str | None = None, url: str = BOT_URL):
    # The token is created on the first command, not at startup: an unwritable state dir must cost only the
    # announce command, never the whole agent (every server entity).
    bearer: list[str] = []

    async def announce(cmd: dict) -> dict:
        if not bearer:
            try:
                bearer.append(await asyncio.to_thread(token, path or token_path()))
            except OSError:
                return {"ok": False, "error": "token_unavailable"}
        body = {"text": cmd.get("text"), "satellites": cmd.get("satellites")}
        try:
            status, data = await asyncio.to_thread(post, url, bearer[0], body)
        except (OSError, ValueError):
            return {"ok": False, "error": "bot_unreachable"}
        if status == 400:
            return {"ok": False, "error": "invalid", "detail": data.get("erro")}
        if status != 200:
            return {"ok": False, "error": "bot_error"}
        return {"data": data}  # the agent core only forwards `data` (and `error`) to the integration

    return {"announce": announce}
