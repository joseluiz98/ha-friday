import asyncio
import unittest

from loguru import logger

import player_guard as pg

OWN = "media_player.escritorio"
BAD = "media_player.f_r_i_d_a_y_office"


def fake_state(states):
    """read_state backed by a dict; a missing id = the entity does not exist (None)."""
    calls = []

    async def read(entity_id):
        calls.append(entity_id)
        return states.get(entity_id)
    read.calls = calls
    return read


class GuardTest(unittest.IsolatedAsyncioTestCase):
    async def test_made_up_player_replaced(self):
        lines = []
        hid = logger.add(lambda m: lines.append(str(m)), level="INFO")
        try:
            args, early = await pg.guard("script__music_play", {"players": [BAD], "query": "x"}, OWN,
                                         fake_state({OWN: "idle"}))
        finally:
            logger.remove(hid)
        self.assertEqual(args, {"players": [OWN], "query": "x"})
        self.assertIsNone(early)
        self.assertTrue(any(f"player corrigido: {BAD} -> {OWN}" in line for line in lines), lines)

    async def test_existing_other_player_untouched(self):
        other = "media_player.escritorio_3"
        args, early = await pg.guard("script__music_play", {"players": [other]}, OWN,
                                     fake_state({OWN: "idle", other: "playing"}))
        self.assertEqual(args, {"players": [other]})
        self.assertIsNone(early)

    async def test_duplicates_dropped(self):
        args, _ = await pg.guard("script__music_play", {"players": [BAD, OWN]}, OWN, fake_state({OWN: "idle"}))
        self.assertEqual(args["players"], [OWN])

    async def test_missing_players_on_play_gets_own(self):
        args, _ = await pg.guard("script__music_play", {"query": "x"}, OWN, fake_state({OWN: "idle"}))
        self.assertEqual(args, {"query": "x", "players": [OWN]})

    async def test_empty_players_on_play_gets_own(self):
        args, _ = await pg.guard("script__music_play", {"players": []}, OWN, fake_state({OWN: "idle"}))
        self.assertEqual(args["players"], [OWN])

    async def test_single_player_script_fixed(self):
        args, _ = await pg.guard("script__music_seek", {"player": BAD, "position": 5}, OWN, fake_state({OWN: "idle"}))
        self.assertEqual(args, {"player": OWN, "position": 5})

    async def test_single_player_script_missing_gets_own(self):
        args, _ = await pg.guard("script__music_shuffle", {"enabled": True}, OWN, fake_state({OWN: "idle"}))
        self.assertEqual(args, {"enabled": True, "player": OWN})

    async def test_to_players_str_and_list(self):
        args, _ = await pg.guard("script__music_transfer", {"to_players": BAD}, OWN, fake_state({OWN: "idle"}))
        self.assertEqual(args["to_players"], OWN)
        args, _ = await pg.guard("script__music_transfer", {"to_players": [BAD]}, OWN, fake_state({OWN: "idle"}))
        self.assertEqual(args["to_players"], [OWN])

    async def test_transfer_without_target_not_filled(self):
        args, _ = await pg.guard("script__music_transfer", {}, OWN, fake_state({OWN: "idle"}))
        self.assertEqual(args, {})

    async def test_unavailable_returns_early(self):
        args, early = await pg.guard("script__music_play", {"players": [BAD]}, OWN, fake_state({OWN: "unavailable"}))
        self.assertEqual(early, {"result": {"ok": False, "reason": "player_unavailable", "players": [OWN]}})
        self.assertEqual(args["players"], [OWN])

    async def test_one_available_player_is_not_early(self):
        other = "media_player.sala"
        _, early = await pg.guard("script__music_play", {"players": [OWN, other]}, OWN,
                                  fake_state({OWN: "unavailable", other: "idle"}))
        self.assertIsNone(early)

    async def test_state_read_failure_propagates_from_guard(self):
        async def boom(_):
            raise RuntimeError("ha down")
        with self.assertRaises(RuntimeError):
            await pg.guard("script__music_play", {"players": [BAD]}, OWN, boom)

    async def test_state_read_failure_passes_through(self):
        async def boom(_):
            raise RuntimeError("ha down")
        orig = {"players": [BAD]}
        args, early = await pg.safe_guard("script__music_play", orig, OWN, boom)
        self.assertEqual(args, {"players": [BAD]})
        self.assertIsNone(early)

    async def test_slow_read_passes_through_within_budget(self):
        async def slow(_):
            await asyncio.sleep(5)
        loop = asyncio.get_running_loop()
        t0 = loop.time()
        args, early = await pg.safe_guard("script__music_play", {"players": [BAD]}, OWN, slow, timeout_s=0.1)
        self.assertLess(loop.time() - t0, 1.0)
        self.assertEqual(args, {"players": [BAD]})
        self.assertIsNone(early)

    async def test_non_music_tool_untouched(self):
        read = fake_state({})
        args, early = await pg.guard("script__tv_canal", {"players": [BAD]}, OWN, read)
        self.assertEqual(args, {"players": [BAD]})
        self.assertIsNone(early)
        self.assertEqual(read.calls, [])

    async def test_no_own_player_untouched(self):
        read = fake_state({})
        args, early = await pg.guard("script__music_play", {"players": [BAD]}, None, read)
        self.assertEqual(args, {"players": [BAD]})
        self.assertIsNone(early)
        self.assertEqual(read.calls, [])

    async def test_none_arguments(self):
        args, _ = await pg.guard("script__music_play", None, OWN, fake_state({OWN: "idle"}))
        self.assertEqual(args, {"players": [OWN]})


class ReadStateHttpTest(unittest.IsolatedAsyncioTestCase):
    async def _serve(self, handler):
        from aiohttp import web
        app = web.Application()
        app.router.add_get("/api/states/{eid}", handler)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        self.addAsyncCleanup(runner.cleanup)
        return f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"

    async def test_200_404_500_and_timeout(self):
        from aiohttp import web

        async def handler(request):
            eid = request.match_info["eid"]
            if eid == "media_player.ok":
                return web.json_response({"state": "idle"})
            if eid == "media_player.slow":
                await asyncio.sleep(1)
            if eid == "media_player.err":
                return web.Response(status=500)
            return web.Response(status=404)
        url = await self._serve(handler)
        self.assertEqual(await pg.read_state_http("media_player.ok", url, "t"), "idle")
        self.assertIsNone(await pg.read_state_http("media_player.nope", url, "t"))
        with self.assertRaises(Exception):
            await pg.read_state_http("media_player.err", url, "t")
        with self.assertRaises(Exception):
            await pg.read_state_http("media_player.slow", url, "t", timeout_s=0.2)


if __name__ == "__main__":
    unittest.main()
