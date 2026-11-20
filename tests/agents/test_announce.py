"""Announce command of the server agent: forwards the text to the bot's /announce."""

import asyncio
import os
import stat

from friday_server_agent import announce as an

URL = "http://127.0.0.1:7860/announce"


def test_token_created_600_and_reused(tmp_path):
    path = str(tmp_path / "announce.token")
    first = an.token(path)
    assert len(first) >= 32
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert an.token(path) == first


def test_token_is_not_logged(tmp_path, caplog):
    caplog.set_level("DEBUG")
    t = an.token(str(tmp_path / "a.token"))
    assert t not in caplog.text


def make(tmp_path, reply=(200, {"id": "x"}), error=None):
    calls = []

    def post(url, bearer, body):
        calls.append((url, bearer, body))
        if error:
            raise error
        return reply

    path = str(tmp_path / "announce.token")
    return an.make_handlers(post, path)["announce"], calls, an.token(path)


def test_posts_with_bearer(tmp_path):
    handler, calls, tok = make(tmp_path, (200, {"id": "a1", "satelites": {"s1": "ok"}, "cache": "miss"}))
    out = asyncio.run(handler({"text": "Oi", "satellites": ["s1"]}))
    assert out == {"data": {"id": "a1", "satelites": {"s1": "ok"}, "cache": "miss"}}
    assert calls == [(URL, tok, {"text": "Oi", "satellites": ["s1"]})]


def test_satellites_default_none(tmp_path):
    handler, calls, _ = make(tmp_path)
    asyncio.run(handler({"text": "Oi"}))
    assert calls[0][2] == {"text": "Oi", "satellites": None}


def test_bot_unreachable(tmp_path):
    handler, _, _ = make(tmp_path, error=ConnectionRefusedError())
    assert asyncio.run(handler({"text": "Oi"})) == {"ok": False, "error": "bot_unreachable"}


def test_invalid_400(tmp_path):
    handler, _, _ = make(tmp_path, (400, {"erro": "texto vazio"}))
    assert asyncio.run(handler({"text": ""})) == {"ok": False, "error": "invalid", "detail": "texto vazio"}


def test_other_status_is_bot_error(tmp_path):
    handler, _, _ = make(tmp_path, (401, {"erro": "não autorizado"}))
    assert asyncio.run(handler({"text": "Oi"}))["ok"] is False
