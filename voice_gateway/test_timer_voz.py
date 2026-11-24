"""Spec 2026-11-24 goal 3: o aviso falado de um timer com nome é sintetizado na criação e mandado ao satélite.
cd voice_gateway && python3 -m unittest -q test_timer_voz"""
import asyncio, os, shutil, tempfile, unittest

import anuncios as an
import timer_voz as tv
import tts_saude

PCM = b"\x01\x00\x02\x00" * 100
SATS = {"sat-0a1b2c": {"comodo": "Sala", "senha": "a" * 43, "ip": "192.0.2.15"}}


class Deps:
    def __init__(self, pasta):
        self.cache = an.Cache(os.path.join(pasta, "cache"))
        self.satelites = SATS
        self.wifi = {"sat-0a1b2c": "192.0.2.8"}
        self.tts_chamadas, self.enviados, self.usos, self.logs = [], [], [], []
        self.tts_erro, self.tts_atraso, self.tts_timeout_s = None, 0, 5.0
        self.respostas = {}  # ip -> "ok" | "sem_timer" | Exception

    def ajustes_tts(self):
        return {"voice": "Aoede", "model": "m", "rate": "normal"}

    async def tts(self, ajustes, texto):
        self.tts_chamadas.append(texto)
        if self.tts_atraso:
            await asyncio.sleep(self.tts_atraso)
        if self.tts_erro:
            raise self.tts_erro
        return PCM, {"input_text": 5, "output_audio": 20}

    async def ip_wifi(self, sid):
        return self.wifi.get(sid)

    async def enviar_voz_timer(self, ip, senha, timer_id, wav):
        assert wav[:4] == b"RIFF"
        self.enviados.append((ip, senha, timer_id))
        r = self.respostas.get(ip, "ok")
        if isinstance(r, Exception):
            raise r
        return r

    def registrar_uso(self, chave, modelo, uso):
        self.usos.append((chave, modelo, uso))

    def registrar(self, linha):
        self.logs.append(linha)


class Base(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d, True)
        antes = os.environ.get("FRIDAY_TTS_HEALTH")
        os.environ["FRIDAY_TTS_HEALTH"] = os.path.join(self.d, "tts-health.json")
        self.addCleanup(lambda: os.environ.__setitem__("FRIDAY_TTS_HEALTH", antes) if antes is not None
                        else os.environ.pop("FRIDAY_TTS_HEALTH", None))
        self.deps = Deps(self.d)


class TestAvisar(Base):
    async def test_named_timer_sends_the_wav_to_the_satellite(self):
        r = await tv.avisar_voz_do_timer(self.deps, "sat-0a1b2c", "t1ab852", "arroz")
        self.assertEqual(r, "ok")
        self.assertEqual(self.deps.tts_chamadas, ["O timer arroz terminou."])
        self.assertEqual(self.deps.enviados, [("192.0.2.15", "a" * 43, "t1ab852")])
        self.assertEqual(self.deps.usos[0][0], "timer-t1ab852")

    async def test_unnamed_timer_sends_nothing(self):
        r = await tv.avisar_voz_do_timer(self.deps, "sat-0a1b2c", "t1ab852", None)
        self.assertEqual(r, "sem_nome")
        self.assertEqual((self.deps.tts_chamadas, self.deps.enviados), ([], []))

    async def test_tts_failure_is_logged_counted_and_does_not_break_creation(self):
        self.deps.tts_erro = RuntimeError("boom")
        r = await tv.avisar_voz_do_timer(self.deps, "sat-0a1b2c", "t1ab852", "arroz")
        self.assertEqual(r, "tts_falhou")
        self.assertEqual(self.deps.enviados, [])
        e = tts_saude.estado()
        self.assertEqual((e["seguidas"], e["origem"], e["ultimo_erro"]), (1, "timer", "RuntimeError"))
        self.assertEqual(self.deps.logs[-1]["resultado"], "tts_falhou")

    async def test_tts_timeout_counts_as_a_failure(self):
        self.deps.tts_atraso, self.deps.tts_timeout_s = 5, 0.05
        self.assertEqual(await tv.avisar_voz_do_timer(self.deps, "sat-0a1b2c", "t1ab852", "arroz"), "tts_falhou")
        self.assertEqual(tts_saude.estado()["seguidas"], 1)

    async def test_a_good_tts_clears_the_streak(self):
        tts_saude.registrar(False, "anuncio", "E")
        await tv.avisar_voz_do_timer(self.deps, "sat-0a1b2c", "t1ab852", "arroz")
        self.assertEqual(tts_saude.estado()["seguidas"], 0)

    async def test_cached_text_does_not_call_tts_again(self):
        await tv.avisar_voz_do_timer(self.deps, "sat-0a1b2c", "t1ab852", "arroz")
        r = await tv.avisar_voz_do_timer(self.deps, "sat-0a1b2c", "t2cd034", "arroz")
        self.assertEqual(r, "ok")
        self.assertEqual(len(self.deps.tts_chamadas), 1)
        self.assertEqual([e[2] for e in self.deps.enviados], ["t1ab852", "t2cd034"])
        self.assertEqual(self.deps.logs[-1]["cache"], "hit")

    async def test_send_failure_is_returned_not_raised(self):
        self.deps.respostas = {"192.0.2.15": ConnectionError("x"), "192.0.2.8": ConnectionError("y")}
        self.assertEqual(await tv.avisar_voz_do_timer(self.deps, "sat-0a1b2c", "t1ab852", "arroz"), "envio_falhou")

    async def test_falls_back_to_the_wifi_address(self):
        self.deps.respostas = {"192.0.2.15": ConnectionError("x")}
        self.assertEqual(await tv.avisar_voz_do_timer(self.deps, "sat-0a1b2c", "t1ab852", "arroz"), "ok")
        self.assertEqual([e[0] for e in self.deps.enviados], ["192.0.2.15", "192.0.2.8"])

    async def test_satellite_saying_the_timer_is_gone_stops_there(self):
        self.deps.respostas = {"192.0.2.15": "sem_timer"}
        self.assertEqual(await tv.avisar_voz_do_timer(self.deps, "sat-0a1b2c", "t1ab852", "arroz"), "sem_timer")
        self.assertEqual(len(self.deps.enviados), 1)

    async def test_unknown_satellite_is_a_clean_failure(self):
        self.assertEqual(await tv.avisar_voz_do_timer(self.deps, "sat-zzzzzz", "t1ab852", "arroz"), "envio_falhou")


class TestTimerCriado(unittest.TestCase):
    def test_extracts_id_and_name(self):
        r = {"ok": True, "timer": {"id": "t1ab852", "nome": "arroz", "duracao_s": 600}}
        self.assertEqual(tv.timer_criado(r), ("t1ab852", "arroz"))
        self.assertEqual(tv.timer_criado({"ok": True, "timer": {"id": "t1ab852", "nome": None}}), ("t1ab852", None))

    def test_failures_and_garbage_are_none(self):
        for ruim in (None, "x", {"ok": False, "erro": "e"}, {"ok": True}, {"ok": True, "timer": {"id": "../x"}},
                     {"ok": True, "timer": {"nome": "arroz"}}):
            self.assertIsNone(tv.timer_criado(ruim), ruim)


class TestFiacao(unittest.TestCase):
    def test_bot_wires_the_notice_after_a_created_timer(self):  # bot.py não importa sem o pipecat: confere o texto
        fonte = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "bot.py")).read()
        for trecho in ("import timer_voz as tvz", "tvz.timer_criado(r)", "tvz.avisar_voz_do_timer(",
                       "async def enviar_voz_timer(", "/timer_voice"):
            self.assertIn(trecho, fonte)


if __name__ == "__main__":
    unittest.main()
