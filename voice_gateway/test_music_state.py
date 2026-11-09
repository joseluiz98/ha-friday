"""cd /srv/friday/app && python3 -m unittest test_music_state -v   (spec 2026-10-21 §6.4: leitura com teto de 0,3 s)"""
import asyncio, time, unittest
from aiohttp import web
import music_state as ms


async def _servidor(handler):
    app = web.Application()
    app.router.add_get("/api/states/{ent}", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    porta = site._server.sockets[0].getsockname()[1]
    return runner, f"http://127.0.0.1:{porta}"


class TestPlayerState(unittest.IsolatedAsyncioTestCase):
    async def test_player_state_returns_the_state(self):
        async def ok(req):
            return web.json_response({"state": "playing", "attributes": {"media_title": "X"}})
        runner, url = await _servidor(ok)
        try:
            r = await ms.player_state("media_player.x", url, "tok")
            self.assertEqual(r["state"], "playing")
        finally:
            await runner.cleanup()

    async def test_player_state_timeout_returns_none(self):
        async def lento(req):
            await asyncio.sleep(1)
            return web.json_response({"state": "playing"})
        runner, url = await _servidor(lento)
        try:
            t = time.monotonic()
            r = await ms.player_state("media_player.x", url, "tok", timeout_s=0.3)
            self.assertIsNone(r)
            self.assertLess(time.monotonic() - t, 0.5)
        finally:
            await runner.cleanup()

    async def test_player_state_error_returns_none(self):
        async def erro(req):
            return web.Response(status=500)
        runner, url = await _servidor(erro)
        try:
            self.assertIsNone(await ms.player_state("media_player.x", url, "tok"))
        finally:
            await runner.cleanup()

    async def test_player_state_unreachable_returns_none(self):
        self.assertIsNone(await ms.player_state("media_player.x", "http://127.0.0.1:1", "tok"))

    async def test_player_state_without_entity_returns_none(self):
        self.assertIsNone(await ms.player_state("", "http://127.0.0.1:1", "tok"))



class TestMusicFactFor(unittest.IsolatedAsyncioTestCase):
    async def test_music_fact_without_player_in_catalog(self):
        async def cat():
            return {"sat-1": {"comodo": "X"}}

        async def est(p):
            raise AssertionError("não deve ler o estado sem player")
        self.assertEqual(await ms.music_fact_for("sat-1", cat, est, lambda p, e: "fato"), "")

    async def test_music_fact_with_player_calls_state_and_builds_fact(self):
        async def cat():
            return {"sat-1": {"player": "media_player.x"}}

        async def est(p):
            return {"state": "idle"}
        self.assertEqual(await ms.music_fact_for("sat-1", cat, est, lambda p, e: f"{p}|{e['state']}"),
                         "media_player.x|idle")

    async def test_music_fact_survives_catalog_failure(self):
        async def cat():
            raise OSError("cifs")
        self.assertEqual(await ms.music_fact_for("sat-1", cat, None, None), "")


if __name__ == "__main__":
    unittest.main()
