"""Announce command of the server agent: forwards the text to the bot's /announce."""

import asyncio
import json
import os
import stat
from pathlib import Path

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


def test_token_is_created_lazily_on_the_first_command(tmp_path):
    path = tmp_path / "announce.token"
    posted = []
    handlers = an.make_handlers(lambda u, b, body: posted.append(b) or (200, {"id": "x"}), str(path))
    assert not path.exists()  # make_handlers (agent startup) must not touch the state dir
    asyncio.run(handlers["announce"]({"text": "Oi"}))
    assert path.exists() and posted == [path.read_text().strip()]


def test_unwritable_state_dir_does_not_crash_and_fails_only_the_command(tmp_path):
    path = tmp_path / "missing-dir" / "announce.token"  # parent does not exist: token() raises OSError
    posted = []
    handlers = an.make_handlers(lambda u, b, body: posted.append(b) or (200, {"id": "x"}), str(path))
    out = asyncio.run(handlers["announce"]({"text": "Oi"}))
    assert out == {"ok": False, "error": "token_unavailable"}
    assert posted == []
    # the agent keeps running: once the folder is writable the next command works
    path.parent.mkdir()
    assert asyncio.run(handlers["announce"]({"text": "Oi"})) == {"data": {"id": "x"}}
    assert posted == [path.read_text().strip()]


def test_request_body_matches_the_bots_contract_fixture(tmp_path):
    """voice_gateway/announce_request.json is the body the bot's validar accepts (voice_gateway/test_anuncios.py
    feeds it to validar on its side): the agent must post exactly those field names."""
    fixture = json.loads((Path(__file__).resolve().parents[2] / "voice_gateway" / "announce_request.json")
                         .read_text(encoding="utf-8"))
    handler, calls, _ = make(tmp_path)
    asyncio.run(handler(dict(fixture)))
    assert calls[0][2] == fixture


def test_http_timeout_leaves_room_for_the_bots_first_round():
    # the bot answers within 18 s (first-round budget); the integration waits 30 s, the agent 25 s
    assert 18 < an.TIMEOUT_S < 30
