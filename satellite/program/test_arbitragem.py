import asyncio, contextlib, io, json, unittest

import arbitragem
from arbitragem import ClienteDisputa, novo_claim_id

CLAIM = {"claim_id": "abc", "wake_word": "friday", "score": 0.9, "voice_dbfs": -30.0}


class FakePost:
    """Injectable `post`: records calls, answers (status, body) or raises, optionally after a delay."""

    def __init__(self, status=200, body=None, delay=0.0, exc=None):
        self.status, self.body, self.delay, self.exc = status, body or {}, delay, exc
        self.calls = []

    async def __call__(self, url, json, headers, timeout_s):
        self.calls.append({"url": url, "json": json, "headers": headers, "timeout_s": timeout_s})
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.exc:
            raise self.exc
        return self.status, self.body


def cliente(post):
    return ClienteDisputa("https://vm:7861", "segredo", post=post)


class DisputaTest(unittest.IsolatedAsyncioTestCase):
    async def test_presence_sets_others_online(self):
        post = FakePost(body={"others_online": 2})
        c = cliente(post)
        self.assertEqual(c.outros_online, 0)
        self.assertFalse(c.precisa_disputar())
        await c.presenca()
        self.assertEqual(c.outros_online, 2)
        self.assertTrue(c.precisa_disputar())
        self.assertEqual(post.calls[0]["url"], "https://vm:7861/satellite_presence")
        self.assertEqual(post.calls[0]["headers"], {"Authorization": "Bearer segredo"})
        self.assertEqual(post.calls[0]["timeout_s"], 2)

    async def test_presence_failure_means_alone(self):
        c = cliente(FakePost(body={"others_online": 3}))
        await c.presenca()
        self.assertEqual(c.outros_online, 3)
        for post in (FakePost(exc=OSError("down")), FakePost(status=404), FakePost(status=401),
                     FakePost(body={"others_online": "x"}), FakePost(body={})):
            c._post = post
            c.outros_online = 3
            await c.presenca()
            self.assertEqual(c.outros_online, 0, post.status)
            self.assertFalse(c.precisa_disputar())

    async def test_presence_loop_never_raises(self):
        post = FakePost(exc=RuntimeError("boom"))
        c = cliente(post)
        tarefa = asyncio.ensure_future(c.laco_presenca(intervalo_s=0.01))
        await asyncio.sleep(0.08)
        self.assertFalse(tarefa.done())
        self.assertGreaterEqual(len(post.calls), 2)
        tarefa.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await tarefa

    async def test_claim_go(self):
        post = FakePost(body={"verdict": "go", "reason": "best", "round_id": "r1"})
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            r = await cliente(post).disputar(CLAIM)
        self.assertEqual(r, {"verdict": "go", "reason": "best", "round_id": "r1"})
        self.assertEqual(post.calls[0]["url"], "https://vm:7861/wake_claim")
        self.assertEqual(post.calls[0]["json"], CLAIM)
        self.assertEqual(post.calls[0]["timeout_s"], 0.3)
        linha = json.loads(buf.getvalue().strip().splitlines()[-1])
        self.assertEqual((linha["arbitration"], linha["verdict"]), ("best", "go"))
        self.assertIn("ms", linha)

    async def test_claim_lost(self):
        post = FakePost(body={"verdict": "lost", "reason": "late", "round_id": "r2"})
        with contextlib.redirect_stdout(io.StringIO()):
            r = await cliente(post).disputar(CLAIM)
        self.assertEqual(r["verdict"], "lost")
        self.assertEqual(r["reason"], "late")

    async def test_claim_timeout_is_go(self):
        buf = io.StringIO()
        t0 = asyncio.get_running_loop().time()
        with contextlib.redirect_stdout(buf):
            r = await cliente(FakePost(delay=0.5, body={"verdict": "lost"})).disputar(CLAIM)
        self.assertLess(asyncio.get_running_loop().time() - t0, 0.35)
        self.assertEqual(r, {"verdict": "go", "reason": "timeout"})
        linha = json.loads(buf.getvalue().strip().splitlines()[-1])
        self.assertEqual((linha["arbitration"], linha["verdict"]), ("timeout", "go"))

    async def test_claim_404_is_go(self):
        for post in (FakePost(status=404), FakePost(status=401), FakePost(status=400),
                     FakePost(exc=OSError("down")), FakePost(body={"verdict": "banana"}), FakePost(body={})):
            with contextlib.redirect_stdout(io.StringIO()):
                r = await cliente(post).disputar(CLAIM)
            self.assertEqual(r, {"verdict": "go", "reason": "timeout"}, post.status)

    async def test_registrar_never_raises(self):
        post = FakePost(body={"verdict": "go"})
        c = cliente(post)
        await c.registrar(CLAIM)
        self.assertEqual(post.calls[0]["json"], dict(CLAIM, record_only=True))
        self.assertEqual(post.calls[0]["timeout_s"], 2)
        self.assertEqual(post.calls[0]["url"], "https://vm:7861/wake_claim")
        self.assertNotIn("record_only", CLAIM)  # the caller's dict is untouched
        for p in (FakePost(exc=OSError("x")), FakePost(exc=asyncio.TimeoutError()), FakePost(status=500)):
            await cliente(p).registrar(CLAIM)

    def test_novo_claim_id(self):
        a, b = novo_claim_id(), novo_claim_id()
        self.assertIsInstance(a, str)
        self.assertTrue(a)
        self.assertNotEqual(a, b)


if __name__ == "__main__":
    unittest.main()
