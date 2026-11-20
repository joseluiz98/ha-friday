"""cd voice_gateway && python3 -m unittest test_anuncios -v   (spec 2026-11-20 satellite announcements, Flow 4)"""
import asyncio, io, json, os, tempfile, time, unittest, wave

import anuncios as an
import canal_casa as cc
import voice_settings as vs

SATS = {
    "sat-0a1b2c": {"comodo": "Sala", "senha": "a" * 43, "ip": "192.0.2.15"},
    "sat-0d1e2f": {"comodo": "Quarto", "senha": "b" * 43, "ip": "192.0.2.16"},
}
PCM = b"\x01\x00\x02\x00" * 100  # 200 frames, 24 kHz mono


class Deps:
    """IO simulado: relógio falso, TTS contado, satélites com respostas roteirizadas por IP."""

    def __init__(self, pasta, ajustes=None):
        self.satelites = dict(SATS)
        self.wifi = {"sat-0a1b2c": "192.0.2.8", "sat-0d1e2f": "192.0.2.9"}
        self.cache = an.Cache(pasta)
        self.ajustes = ajustes or vs.tts({}, ambiente={})
        self.roteiro = {}  # ip -> lista de respostas (str ou Exception); a última se repete
        self.enviados = []  # (ip, anuncio_id, tamanho do corpo)
        self.tts_chamadas = []
        self.tts_erro = None
        self.tts_atraso = 0
        self.atraso_envio = {}  # ip -> segundos (reais) antes de a Pi responder
        self.primeira_rodada_s = 0.4  # o real é 18 s; aqui cabe no teste
        self.registros, self.usos = [], []
        self.agora_ = 1000.0
        self.dormidas = []

    def ajustes_tts(self):
        return self.ajustes

    async def tts(self, ajustes, texto):
        self.tts_chamadas.append((dict(ajustes), texto))
        if self.tts_atraso:
            await asyncio.sleep(self.tts_atraso)
        if self.tts_erro:
            raise self.tts_erro
        return PCM, {"input_text": 10, "output_audio": 50}

    async def ip_wifi(self, sid):
        return self.wifi.get(sid)

    async def enviar(self, ip, senha, anuncio_id, wav):
        assert wav[:4] == b"RIFF"
        self.enviados.append((ip, anuncio_id, len(wav)))
        if self.atraso_envio.get(ip):
            await asyncio.sleep(self.atraso_envio[ip])
        fila = self.roteiro.get(ip)
        if not fila:
            raise ConnectionError(ip)
        r = fila.pop(0) if len(fila) > 1 else fila[0]
        if isinstance(r, Exception):
            raise r
        return r

    def registrar(self, linha):
        self.registros.append(linha)

    def registrar_uso(self, anuncio_id, modelo, uso):
        self.usos.append((anuncio_id, modelo, uso))

    def agora(self):
        return self.agora_

    async def dormir(self, s):
        self.dormidas.append(s)
        self.agora_ += s
        await asyncio.sleep(0)


class Base(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.deps = Deps(os.path.join(self._tmp.name, "cache"))

    async def anunciar(self, corpo=None, **kw):
        r = await an.anunciar(corpo or {"text": "O jantar está pronto"}, self.deps)
        await an.aguardar_pendentes()
        return r


class TestValidar(unittest.TestCase):
    def test_contrato_do_pedido_do_agente(self):
        """announce_request.json é o corpo que o agente do servidor posta (tests/agents/test_announce.py o confere
        do outro lado): o bot tem de aceitá-lo como está. Dois lados com nomes de campo diferentes recusavam
        todo anúncio real como `invalid`."""
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "announce_request.json"),
                  encoding="utf-8") as f:
            corpo = json.load(f)
        self.assertEqual(set(corpo), {"text", "satellites"})
        self.assertEqual(an.validar(corpo, SATS), (corpo["text"], corpo["satellites"]))

    def test_sem_satelites_vale_todos(self):
        self.assertEqual(an.validar({"text": " Oi "}, SATS), ("Oi", ["sat-0a1b2c", "sat-0d1e2f"]))

    def test_lista_explicita(self):
        self.assertEqual(an.validar({"text": "Oi", "satellites": ["sat-0d1e2f"]}, SATS)[1], ["sat-0d1e2f"])

    def test_recusas(self):
        for corpo in (None, [], {"text": "   "}, {"text": "x" * 501}, {"text": 5}, {},
                      {"text": "Oi", "satellites": ["sat-ffffff"]}, {"text": "Oi", "satellites": "sat-0a1b2c"},
                      {"text": "Oi", "satellites": []}):
            with self.assertRaises(ValueError, msg=repr(corpo)):
                an.validar(corpo, SATS)

    def test_limite_500_vale(self):
        self.assertEqual(len(an.validar({"text": "x" * 500}, SATS)[0]), 500)


class TestWav(unittest.TestCase):
    def test_48k_estereo_com_o_dobro_de_quadros(self):
        w = wave.open(io.BytesIO(an.wav_48k_estereo(PCM)))
        self.assertEqual((w.getframerate(), w.getnchannels(), w.getsampwidth()), (48000, 2, 2))
        self.assertEqual(w.getnframes(), 2 * len(PCM) // 2)
        dados = w.readframes(w.getnframes())
        self.assertEqual(dados[0:2], dados[2:4])  # canais iguais


class TestCache(unittest.TestCase):
    def test_gravar_ler_e_limite(self):
        with tempfile.TemporaryDirectory() as t:
            c = an.Cache(os.path.join(t, "c"), limite=3)
            self.assertIsNone(c.ler("a" * 64))
            for i, k in enumerate("abcd"):
                c.gravar(k * 64, b"RIFF" + bytes([i]))
                os.utime(os.path.join(t, "c", k * 64 + ".wav"), (1000 + i, 1000 + i))
            c.gravar("e" * 64, b"RIFFe")
            nomes = sorted(os.listdir(os.path.join(t, "c")))
            self.assertEqual(len(nomes), 3)
            self.assertIsNone(c.ler("a" * 64))
            self.assertIsNone(c.ler("b" * 64))
            self.assertEqual(c.ler("e" * 64), b"RIFFe")


class TestChave(unittest.TestCase):
    def test_voz_ritmo_modelo_e_texto_mudam_a_chave(self):
        base = vs.tts({}, ambiente={})
        k = an.chave(base, "oi")
        self.assertEqual(len(k), 64)
        self.assertEqual(k, an.chave(dict(base), "oi"))
        for outro in (dict(base, voice="Kore"), dict(base, rate="fast"), dict(base, model="models/x")):
            self.assertNotEqual(k, an.chave(outro, "oi"))
        self.assertNotEqual(k, an.chave(base, "oi!"))


class TestAnunciar(Base):
    def ok(self, ip="192.0.2.15", r="vou_tocar"):
        self.deps.roteiro[ip] = [r]

    async def test_fluxo_feliz_e_log_de_uso(self):
        self.ok("192.0.2.15"); self.ok("192.0.2.16", "na_fila")
        r = await self.anunciar()
        self.assertEqual(r["satelites"], {"sat-0a1b2c": "vou_tocar", "sat-0d1e2f": "na_fila"})
        self.assertEqual(r["cache"], "miss")
        self.assertRegex(r["id"], r"^[a-z0-9]{8,32}$")
        self.assertEqual(len(self.deps.tts_chamadas), 1)
        self.assertEqual(self.deps.tts_chamadas[0][1], "O jantar está pronto")
        self.assertEqual(self.deps.usos[0][0], r["id"])
        self.assertEqual(self.deps.usos[0][1], self.deps.ajustes["model"])
        self.assertEqual({e[1] for e in self.deps.enviados}, {r["id"]})
        linha = self.deps.registros[0]
        self.assertEqual((linha["tipo"], linha["id"], linha["cache"]), ("anuncio", r["id"], "miss"))
        self.assertNotIn("jantar", json.dumps(linha))  # o texto não vai para o log

    async def test_cache_hit_nao_chama_o_tts(self):
        self.ok("192.0.2.15"); self.ok("192.0.2.16")
        await self.anunciar()
        r2 = await self.anunciar()
        self.assertEqual(r2["cache"], "hit")
        self.assertEqual(len(self.deps.tts_chamadas), 1)
        self.assertEqual(len(self.deps.usos), 1)

    async def test_mudar_ritmo_ou_voz_erra_o_cache_e_o_tts_recebe_os_valores(self):
        self.ok("192.0.2.15"); self.ok("192.0.2.16")
        with tempfile.TemporaryDirectory() as t:
            arq = os.path.join(t, "voice-settings.json")
            self.deps.ajustes_tts = lambda: vs.tts(vs.ler(arq), ambiente={})
            await self.anunciar()
            self.assertEqual(self.deps.tts_chamadas[-1][0]["rate"], "normal")
            with open(arq, "w") as f:
                json.dump({"speaking_rate": "slow"}, f)
            r = await self.anunciar()
            self.assertEqual(r["cache"], "miss")
            self.assertEqual(self.deps.tts_chamadas[-1][0]["rate"], "slow")
            with open(arq, "w") as f:
                json.dump({"speaking_rate": "slow", "voice": "Kore"}, f)
            r = await self.anunciar()
            self.assertEqual(r["cache"], "miss")
            self.assertEqual(self.deps.tts_chamadas[-1][0]["voice"], "Kore")
            self.assertEqual(len(self.deps.tts_chamadas), 3)
            self.assertEqual((await self.anunciar())["cache"], "hit")

    async def test_texto_invalido_nao_chama_o_tts(self):
        for corpo in ({"text": "   "}, {"text": "x" * 501}):
            with self.assertRaises(ValueError):
                await an.anunciar(corpo, self.deps)
        self.assertEqual(self.deps.tts_chamadas, [])
        self.assertEqual(self.deps.enviados, [])

    async def test_tts_falha_todos_erro(self):
        self.deps.tts_erro = RuntimeError("boom")
        r = await self.anunciar()
        self.assertEqual(r["satelites"], {"sat-0a1b2c": "erro", "sat-0d1e2f": "erro"})
        self.assertEqual(self.deps.enviados, [])
        self.assertEqual(self.deps.registros[0]["tts_erro"], "RuntimeError")

    async def test_tts_estoura_o_prazo(self):
        self.deps.tts_atraso, self.deps.tts_timeout_s = 5, 0.05
        r = await self.anunciar()
        self.assertEqual(set(r["satelites"].values()), {"erro"})

    async def test_cabo_falha_tenta_o_wifi(self):
        self.ok("192.0.2.8"); self.ok("192.0.2.16")
        r = await self.anunciar()
        self.assertEqual(r["satelites"]["sat-0a1b2c"], "vou_tocar")
        self.assertEqual([e[0] for e in self.deps.enviados if e[0] in ("192.0.2.15", "192.0.2.8")],
                         ["192.0.2.15", "192.0.2.8"])

    async def test_404_sem_suporte_e_sem_reenvio(self):
        self.ok("192.0.2.15", "sem_suporte"); self.ok("192.0.2.16")
        r = await self.anunciar()
        self.assertEqual(r["satelites"]["sat-0a1b2c"], "sem_suporte")
        self.assertEqual(self.deps.dormidas, [])
        self.assertEqual(len([e for e in self.deps.enviados if e[0] == "192.0.2.15"]), 1)

    async def test_resposta_estranha_ou_erro_http_e_erro_final(self):
        self.ok("192.0.2.15", "erro"); self.ok("192.0.2.16", "qualquer_coisa")
        r = await self.anunciar()
        self.assertEqual(set(r["satelites"].values()), {"erro"})
        self.assertEqual(self.deps.dormidas, [])

    async def test_recusa_depois_aceita_na_segunda_tentativa(self):
        self.deps.roteiro["192.0.2.15"] = [ConnectionError("x"), "vou_tocar"]  # cabo falha 1x, depois aceita
        self.deps.roteiro["192.0.2.8"] = [ConnectionError("w")]
        self.ok("192.0.2.16")
        r = await self.anunciar()
        self.assertEqual(r["satelites"]["sat-0a1b2c"], "reenviando")
        self.assertEqual(r["satelites"]["sat-0d1e2f"], "vou_tocar")
        self.assertEqual(self.deps.dormidas, [15])
        final = [l for l in self.deps.registros if l.get("tipo") == "anuncio_final"]
        self.assertEqual(len(final), 1)
        self.assertEqual((final[0]["satelite"], final[0]["resposta"], final[0]["id"]),
                         ("sat-0a1b2c", "vou_tocar", r["id"]))

    async def test_nunca_entrega_vira_nao_entregue_aos_300s(self):
        self.ok("192.0.2.16")
        r = await self.anunciar()  # o outro satélite não responde em IP nenhum
        self.assertEqual(r["satelites"]["sat-0a1b2c"], "reenviando")
        self.assertEqual(sum(self.deps.dormidas), 315)
        tentativas = len([e for e in self.deps.enviados if e[0] == "192.0.2.15"])
        self.assertEqual(tentativas, 1 + 20)  # imediata + a cada 15 s até 300 s
        final = [l for l in self.deps.registros if l.get("tipo") == "anuncio_final"]
        self.assertEqual(final[0]["resposta"], "nao_entregue")

    async def test_responde_dentro_da_primeira_rodada_com_tts_e_envios_lentos(self):
        """G2-b: TTS lento + um satélite lento: a resposta sai no orçamento da 1ª rodada (18 s no real, 0,4 s aqui)
        com o lento em `reenviando`; ele segue em segundo plano e o resultado final vai para o log."""
        self.deps.tts_atraso = 0.15
        self.ok("192.0.2.15"); self.ok("192.0.2.16")
        self.deps.atraso_envio["192.0.2.15"] = 1.0
        t0 = time.monotonic()
        r = await an.anunciar({"text": "Oi"}, self.deps)
        gasto = time.monotonic() - t0
        self.assertLess(gasto, 0.4 + 0.25)
        self.assertGreaterEqual(gasto, 0.35)  # esperou o orçamento, não respondeu antes da hora
        self.assertEqual(r["satelites"], {"sat-0a1b2c": "reenviando", "sat-0d1e2f": "vou_tocar"})
        self.assertEqual(self.deps.registros[0]["satelites"], r["satelites"])
        await an.aguardar_pendentes()
        final = [l for l in self.deps.registros if l.get("tipo") == "anuncio_final"]
        self.assertEqual([(l["satelite"], l["resposta"]) for l in final], [("sat-0a1b2c", "vou_tocar")])
        # a primeira tentativa continuou (não foi cancelada nem repetida com o mesmo id)
        self.assertEqual(len([e for e in self.deps.enviados if e[0] == "192.0.2.15"]), 1)

    async def test_envio_lento_que_falha_depois_do_orcamento_segue_o_reenvio_normal(self):
        self.deps.atraso_envio["192.0.2.15"] = 0.6
        self.deps.roteiro["192.0.2.15"] = [ConnectionError("x"), "vou_tocar"]
        self.deps.roteiro["192.0.2.8"] = [ConnectionError("w")]
        self.ok("192.0.2.16")
        r = await an.anunciar({"text": "Oi"}, self.deps)
        self.assertEqual(r["satelites"]["sat-0a1b2c"], "reenviando")
        await an.aguardar_pendentes()
        final = [l for l in self.deps.registros if l.get("tipo") == "anuncio_final"]
        self.assertEqual([l["resposta"] for l in final], ["vou_tocar"])
        self.assertEqual(self.deps.dormidas, [15])

    async def test_tarefas_de_reenvio_ficam_referenciadas(self):
        self.deps.roteiro["192.0.2.16"] = ["vou_tocar"]
        await an.anunciar({"text": "Oi"}, self.deps)
        self.assertGreaterEqual(len(an._em_curso), 1)
        await an.aguardar_pendentes()
        self.assertEqual(len(an._em_curso), 0)


class TestPrazoDeEnvio(unittest.TestCase):
    def test_prazo_cresce_com_o_corpo(self):
        """G2-c: conexão 3 s; total 5 s + 1 s por 500 KB (6,7 MB de WAV não cabem em 5 s no Wi-Fi da Pi)."""
        self.assertEqual(an.prazo_envio(0), (3.0, 5.0))
        self.assertEqual(an.prazo_envio(500_000), (3.0, 6.0))
        c, total = an.prazo_envio(6_700_000)
        self.assertEqual(c, 3.0)
        self.assertAlmostEqual(total, 5.0 + 6_700_000 / 500_000)

    def test_bot_usa_o_prazo(self):
        t = open(os.path.join(os.path.dirname(__file__), "bot.py")).read()
        corpo = t.split("class DepsAnuncio", 1)[1].split("async def telegram_direto", 1)[0]
        self.assertIn("an.prazo_envio(len(wav))", corpo)
        self.assertIn("sock_connect=", corpo)
        self.assertNotIn("ClientTimeout(total=5)", corpo)


class TestToken(unittest.TestCase):
    def test_token_confere(self):
        self.assertTrue(cc.token_confere("segredo", "Bearer segredo"))
        self.assertFalse(cc.token_confere("segredo", "Bearer outro"))
        self.assertFalse(cc.token_confere("", "Bearer "))
        self.assertFalse(cc.token_confere("segredo", None))


class TestBotRota(unittest.TestCase):
    def test_rota_e_fiacao_no_bot(self):
        t = open(os.path.join(os.path.dirname(__file__), "bot.py")).read()
        self.assertIn('@app.post("/announce")', t)
        corpo = t.split('@app.post("/announce")', 1)[1].split("@app.", 1)[0]
        self.assertIn("cc.token_confere(_ler_token_announce()", corpo)
        self.assertIn("status_code=401", corpo)
        self.assertIn("except ValueError", corpo)
        self.assertIn("status_code=400", corpo)
        self.assertIn("an.anunciar(", corpo)


if __name__ == "__main__":
    unittest.main()
