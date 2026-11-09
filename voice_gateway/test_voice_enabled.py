"""Global voice switch in the Voice Gateway (Task 6, spec 2026-10-29).
cd /srv/friday/app && python3 -m unittest test_voice_enabled -v
bot.py does not boot in tests: the two functions are extracted by AST and run against a bare FastAPI app."""
import ast, importlib.util, os, tempfile, unittest

AQUI = os.path.dirname(os.path.abspath(__file__))


def carregar():
    with open(os.path.join(AQUI, "bot.py"), encoding="utf-8") as f:
        arvore = ast.parse(f.read())
    alvo = {"voice_enabled", "registrar_recusa_voz"}
    nos = [n for n in arvore.body if isinstance(n, ast.FunctionDef) and n.name in alvo]
    assert {n.name for n in nos} == alvo
    ns = {"os": os}
    exec(compile(ast.Module(nos, []), "bot.py", "exec"), ns)
    return ns


class TestVoiceEnabled(unittest.TestCase):
    def setUp(self):
        self.ns = carregar()
        self.dir = tempfile.TemporaryDirectory()
        self.arq = os.path.join(self.dir.name, "voice-enabled")

    def tearDown(self):
        self.dir.cleanup()

    def escrever(self, txt):
        with open(self.arq, "w") as f:
            f.write(txt)

    def test_missing_file_means_enabled(self):
        self.assertTrue(self.ns["voice_enabled"](self.arq))

    def test_on_off(self):
        self.escrever("on\n")
        self.assertTrue(self.ns["voice_enabled"](self.arq))
        self.escrever("off\n")
        self.assertFalse(self.ns["voice_enabled"](self.arq))
        self.escrever(" OFF ")
        self.assertFalse(self.ns["voice_enabled"](self.arq))

    def test_corrupt_bytes_fail_open(self):
        with open(self.arq, "wb") as f:
            f.write(b"\xff\xfe\x80off")
        self.assertTrue(self.ns["voice_enabled"](self.arq))

    @unittest.skipUnless(importlib.util.find_spec("pipecat"), "needs pipecat: runs in the friday image (scripts/test-all.sh)")
    def test_offer_refused_when_voice_disabled(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        app = FastAPI()
        vistos = []

        @app.post("/start")
        def start():
            vistos.append("start"); return {"ok": 1}

        @app.post("/api/offer")
        def offer():
            vistos.append("offer"); return {"ok": 1}

        @app.post("/sessions/abc/api/offer")
        def offer_s():
            vistos.append("offer_s"); return {"ok": 1}

        @app.patch("/api/offer")
        def ice():
            vistos.append("ice"); return {"ok": 1}

        @app.post("/deliver")
        def deliver():
            vistos.append("deliver"); return {"ok": 1}

        self.ns["registrar_recusa_voz"](app, self.arq)
        c = TestClient(app)
        self.escrever("off\n")
        for caminho in ("/start", "/api/offer", "/sessions/abc/api/offer"):
            r = c.post(caminho)
            self.assertEqual(r.status_code, 503, caminho)
            self.assertEqual(r.json(), {"error": "voice_disabled"})
        self.assertEqual(vistos, [])
        # ongoing conversation (ICE patch) and the house channel are untouched
        self.assertEqual(c.patch("/api/offer").status_code, 200)
        self.assertEqual(c.post("/deliver").status_code, 200)
        # read on each offer: switching on takes effect without restart
        self.escrever("on\n")
        self.assertEqual(c.post("/api/offer").status_code, 200)

    def test_wired_in_main(self):
        with open(os.path.join(AQUI, "bot.py"), encoding="utf-8") as f:
            t = f.read()
        self.assertIn("registrar_recusa_voz(app_do_runner", t)
        self.assertIn("FRIDAY_VOICE_ENABLED_FILE", t)


if __name__ == "__main__":
    unittest.main()
