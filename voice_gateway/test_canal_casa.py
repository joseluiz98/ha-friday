"""cd /srv/friday/app && python3 -m unittest test_canal_casa -v   (spec 2026-10-12 §3)"""
import asyncio, json, os, stat, tempfile, unittest
import canal_casa as cc

SAT = {"sat-0a1b2c": {"comodo": "Escritório", "senha": "s" * 43, "ip": "192.0.2.15"}}


class Deps:
    """IO simulado: presença do Alex, IP do Wi-Fi, campainha da Pi e registro."""

    def __init__(self, presenca="home", respostas=None, ip_wifi="192.0.2.8"):
        self.entregas = cc.Entregas()
        self.satelites = dict(SAT)
        self._presenca, self._ip_wifi = presenca, ip_wifi
        self.respostas = {"192.0.2.15": "vou_abrir"} if respostas is None else respostas
        self.tocadas, self.registros = [], []
        self.espera_conexao_s, self.espera_fim_s = 0.5, 0.5

    async def presenca(self):
        if isinstance(self._presenca, Exception):
            raise self._presenca
        return self._presenca

    async def ip_wifi(self, sid):
        return self._ip_wifi

    async def campainha(self, ip, senha, eid):
        self.tocadas.append(ip)
        r = self.respostas.get(ip)
        if r is None or isinstance(r, Exception):
            raise ConnectionError(ip)
        return r

    def registrar(self, linha):
        self.registros.append(linha)


async def simular_satelite(deps, atender=True, conectar=True, atraso=0.05):
    """O que o bot faz quando a Pi abre a conversa de entrega."""
    for _ in range(200):  # até 2 s: se o bot não criou a entrega (ex.: "fora de casa"), não há o que simular
        if deps.entregas.ativas():
            break
        await asyncio.sleep(0.01)
    else:
        return
    eid = deps.entregas.ativas()[0]
    await asyncio.sleep(atraso)
    if conectar:
        e = deps.entregas.conectou(eid)
        assert e.texto
        if atender:
            deps.entregas.atendida(eid)
        deps.entregas.terminou(eid)


class TestValidar(unittest.TestCase):
    def test_pedido_valido(self):
        self.assertEqual(cc.validar_pedido({"destino": "jose", "texto": " Roupa ", "tipo": "lembrete"}),
                         ("jose", "Roupa", "lembrete"))
        self.assertEqual(cc.validar_pedido({"destino": "satelite:sat-0a1b2c", "texto": "x"})[2], "aviso")

    def test_pedido_invalido(self):
        for ruim in (None, [], {"destino": "bia", "texto": "x"}, {"destino": "jose"}, {"destino": "jose", "texto": ""},
                     {"destino": "jose", "texto": "x" * 1001}, {"destino": "satelite:../x", "texto": "x"},
                     {"destino": "jose", "texto": "x", "tipo": "spam"}):
            with self.assertRaises(ValueError, msg=ruim):
                cc.validar_pedido(ruim)


class TestEntregar(unittest.IsolatedAsyncioTestCase):
    async def test_atendida_e_entregue(self):
        d = Deps()
        r, _ = await asyncio.gather(cc.entregar({"destino": "jose", "texto": "Roupa"}, d), simular_satelite(d))
        self.assertEqual(r, {"resultado": "entregue", "satelite": "sat-0a1b2c"})
        self.assertEqual(d.registros[-1]["resultado"], "entregue")
        self.assertEqual(d.entregas.ativas(), [])

    async def test_ninguem_responde_e_nao_entregue(self):
        d = Deps()
        r, _ = await asyncio.gather(cc.entregar({"destino": "jose", "texto": "Roupa"}, d),
                                    simular_satelite(d, atender=False))
        self.assertEqual(r["resultado"], "nao_entregue")

    async def test_pi_nao_abre_a_conversa_e_nao_entregue(self):
        d = Deps()
        r, _ = await asyncio.gather(cc.entregar({"destino": "jose", "texto": "Roupa"}, d),
                                    simular_satelite(d, conectar=False))
        self.assertEqual(r["resultado"], "nao_entregue")

    async def test_jose_fora_de_casa(self):
        d = Deps(presenca="not_home")
        self.assertEqual((await cc.entregar({"destino": "jose", "texto": "x"}, d))["resultado"], "fora_de_casa")
        self.assertEqual(d.tocadas, [])

    async def test_ha_fora_do_ar_tenta_o_satelite(self):
        d = Deps(presenca=ConnectionError("HA"))
        r, _ = await asyncio.gather(cc.entregar({"destino": "jose", "texto": "x"}, d), simular_satelite(d))
        self.assertEqual(r["resultado"], "entregue")

    async def test_presenca_desconhecida_tenta_o_satelite(self):
        # HA recém-reiniciado: a pessoa nasce "unknown"; não é "fora de casa"
        for estado in ("unknown", "unavailable"):
            d = Deps(presenca=estado)
            r, _ = await asyncio.gather(cc.entregar({"destino": "jose", "texto": "x"}, d), simular_satelite(d))
            self.assertEqual(r["resultado"], "entregue", estado)

    async def test_zona_diferente_de_casa_e_fora(self):
        d = Deps(presenca="Trabalho")
        self.assertEqual((await cc.entregar({"destino": "jose", "texto": "x"}, d))["resultado"], "fora_de_casa")

    async def test_destino_satelite_ignora_presenca(self):
        d = Deps(presenca="not_home")
        r, _ = await asyncio.gather(cc.entregar({"destino": "satelite:sat-0a1b2c", "texto": "x"}, d),
                                    simular_satelite(d))
        self.assertEqual(r["resultado"], "entregue")

    async def test_nao_perturbe_responde_calado(self):
        d = Deps(respostas={"192.0.2.15": "calado"})
        self.assertEqual((await cc.entregar({"destino": "jose", "texto": "x"}, d))["resultado"], "calado")

    async def test_cabo_mudo_usa_o_ip_do_wifi(self):
        d = Deps(respostas={"192.0.2.15": None, "192.0.2.8": "vou_abrir"})
        r, _ = await asyncio.gather(cc.entregar({"destino": "jose", "texto": "x"}, d), simular_satelite(d))
        self.assertEqual((r["resultado"], d.tocadas), ("entregue", ["192.0.2.15", "192.0.2.8"]))

    async def test_satelite_fora_do_ar(self):
        d = Deps(respostas={}, ip_wifi=None)
        self.assertEqual((await cc.entregar({"destino": "jose", "texto": "x"}, d))["resultado"], "sem_satelite")

    async def test_satelite_desconhecido(self):
        d = Deps()
        r = await cc.entregar({"destino": "satelite:sat-000000", "texto": "x"}, d)
        self.assertEqual(r["resultado"], "sem_satelite")

    async def test_sem_satelite_cadastrado(self):
        d = Deps()
        d.satelites = {}
        self.assertEqual((await cc.entregar({"destino": "jose", "texto": "x"}, d))["resultado"], "sem_satelite")

    async def test_resposta_estranha_da_pi_e_erro(self):
        d = Deps(respostas={"192.0.2.15": "talvez"})
        self.assertEqual((await cc.entregar({"destino": "jose", "texto": "x"}, d))["resultado"], "erro")

    async def test_pedido_invalido_e_erro_sem_tocar(self):
        d = Deps()
        r = await cc.entregar({"destino": "jose"}, d)
        self.assertEqual(r["resultado"], "erro")
        self.assertEqual(d.tocadas, [])

    async def test_conexao_de_entrega_desconhecida(self):
        d = Deps()
        self.assertIsNone(d.entregas.conectou("naoexiste1"))


class TestSatelites(unittest.TestCase):
    def test_ler_cadastro_valida(self):
        with tempfile.TemporaryDirectory() as dd:
            p = os.path.join(dd, "satelites.json")
            with open(p, "w") as f:
                json.dump({"sat-0a1b2c": SAT["sat-0a1b2c"], "lixo": {"senha": "x"}, "sat-111111": {"ip": "1"}}, f)
            self.assertEqual(list(cc.ler_satelites(p)), ["sat-0a1b2c"])
        self.assertEqual(cc.ler_satelites("/nao/existe.json"), {})

    def test_autenticar_satelite_pela_senha_da_porta(self):
        self.assertEqual(cc.autenticar_satelite(SAT, "Bearer " + "s" * 43), "sat-0a1b2c")
        for ruim in ("Bearer errada", "", None, "s" * 43):
            self.assertIsNone(cc.autenticar_satelite(SAT, ruim))

    def test_token_do_canal(self):
        self.assertTrue(cc.token_confere("abc", "Bearer abc"))
        for ruim in ("Bearer abd", "abc", None, ""):
            self.assertFalse(cc.token_confere("abc", ruim))
        self.assertFalse(cc.token_confere("", "Bearer "))


class TestSatellitesWithoutBlocking(unittest.IsolatedAsyncioTestCase):
    """Revisão final de 19/10: com o Samba fora, o open() de /share/friday/satellites.json prendia o laço de eventos do
    bot (todas as sessões de voz) por ~25 s a cada aviso. A leitura vai para uma thread com prazo; estourou, vale o
    cache local."""

    async def test_stuck_share_falls_back_to_cache_without_blocking_the_loop(self):
        import threading, time
        with tempfile.TemporaryDirectory() as dd:
            cache = os.path.join(dd, "satellites.cache.json")
            with open(cache, "w") as f:
                json.dump({"sat-0a1b2c": SAT["sat-0a1b2c"]}, f)
            release = threading.Event()
            self.addCleanup(release.set)

            def stuck_read(primary, cache_path):
                release.wait(5)
                return {}

            ticks = []

            async def ticker():
                while True:
                    ticks.append(time.monotonic())
                    await asyncio.sleep(0.02)

            t = asyncio.create_task(ticker())
            start = time.monotonic()
            got = await cc.read_satellites_without_blocking("/share/x.json", cache, timeout_s=0.3, read=stuck_read)
            elapsed = time.monotonic() - start
            t.cancel()
            release.set()
            self.assertEqual(list(got), ["sat-0a1b2c"])
            self.assertLess(elapsed, 1.0)
            self.assertGreater(len(ticks), 5)  # o laço seguiu girando enquanto a leitura estava presa

    async def test_normal_read_returns_primary(self):
        got = await cc.read_satellites_without_blocking("p", "c", read=lambda p, c: {"sat-0a1b2c": SAT["sat-0a1b2c"]})
        self.assertEqual(list(got), ["sat-0a1b2c"])


class TestAviso(unittest.TestCase):
    def test_texto_do_timer_nao_atendido(self):
        t = cc.texto_aviso({"tipo": "timer_nao_atendido", "nome": "arroz", "duracao_s": 600,
                            "venceu_em": 1791849180.0}, SAT["sat-0a1b2c"])
        self.assertIn("arroz", t)
        self.assertIn("10 minutos", t)
        self.assertIn("Escritório", t)
        self.assertRegex(t, r"\d\d:\d\d")

    def test_texto_sem_nome(self):
        t = cc.texto_aviso({"tipo": "timer_nao_atendido", "nome": None, "duracao_s": 60}, SAT["sat-0a1b2c"])
        self.assertIn("sem nome", t)

    def test_tipo_desconhecido_recusa(self):
        for ruim in ({"tipo": "qualquer"}, {}, None, {"tipo": "timer_nao_atendido", "duracao_s": "x"}):
            with self.assertRaises(ValueError):
                cc.texto_aviso(ruim, SAT["sat-0a1b2c"])

    def test_nome_do_timer_nao_entra_cru(self):
        t = cc.texto_aviso({"tipo": "timer_nao_atendido", "nome": "x" * 200, "duracao_s": 60}, SAT["sat-0a1b2c"])
        self.assertLess(len(t), 250)


class TestTelegram(unittest.IsolatedAsyncioTestCase):
    async def test_direto_primeiro(self):
        feitos = []

        async def direto(t):
            feitos.append(("direto", t))

        async def cerebro(t):
            feitos.append(("cerebro", t))
        self.assertEqual(await cc.mandar_telegram("oi", direto, cerebro), "telegram")
        self.assertEqual(feitos, [("direto", "oi")])

    async def test_cai_no_cerebro_se_o_direto_falha(self):
        async def direto(t):
            raise RuntimeError("sem a ferramenta")

        feitos = []

        async def cerebro(t):
            feitos.append(t)
        self.assertEqual(await cc.mandar_telegram("oi", direto, cerebro), "cerebro")
        self.assertEqual(feitos, ["oi"])

    async def test_os_dois_falham(self):
        async def falha(t):
            raise RuntimeError("x")
        self.assertEqual(await cc.mandar_telegram("oi", falha, falha), "falhou")


class TestLembreteDoAgendador(unittest.TestCase):
    """POST /reminder: o OpenClaw avisa a conclusão de um lembrete (delivery.completionDestination webhook)."""

    def evento(self, **kw):
        e = {"action": "finished", "jobId": "e256aeec", "status": "ok",
             "summary": "Alex, lembrete das 18:30: hora de testar."}
        e.update(kw)
        return e

    def test_vira_pedido_de_lembrete_para_o_jose(self):
        self.assertEqual(cc.pedido_do_lembrete(self.evento()),
                         {"destino": "jose", "tipo": "lembrete", "texto": "Alex, lembrete das 18:30: hora de testar."})

    def test_pedido_passa_na_validacao_da_entrega(self):
        self.assertEqual(cc.validar_pedido(cc.pedido_do_lembrete(self.evento()))[2], "lembrete")

    def test_texto_longo_e_cortado_no_limite_da_entrega(self):
        p = cc.pedido_do_lembrete(self.evento(summary="a" * 3000))
        self.assertEqual(len(p["texto"]), cc.MAX_TEXTO)

    def test_ignora_o_que_nao_e_lembrete_concluido(self):
        for ruim in (None, [], {}, self.evento(status="error"), self.evento(action="started"),
                     self.evento(summary=""), self.evento(summary="   "), self.evento(summary=None),
                     self.evento(summary="NO_REPLY")):
            self.assertIsNone(cc.pedido_do_lembrete(ruim), ruim)


class TestAvisoCadastro(unittest.TestCase):
    """Spec 2026-10-15 D18, D25."""

    def test_aprovado(self):
        t = cc.texto_aviso({"tipo": "cadastro_voz", "nome": "Bia", "aprovado": True, "motivo": None,
                            "numeros": {"nova": "9/10", "falsas_barradas": "28/31"}}, SAT["sat-0a1b2c"])
        self.assertIn("a voz de Bia foi aprendida", t)
        self.assertIn("9/10", t); self.assertIn("28/31", t)

    def test_reprovado(self):
        t = cc.texto_aviso({"tipo": "cadastro_voz", "nome": "Bia", "aprovado": False,
                            "motivo": "tentativas da pessoa nova aceitas: 6 de 10", "numeros": {}}, SAT["sat-0a1b2c"])
        self.assertIn("não foi aprendida", t); self.assertIn("6 de 10", t)

    def test_invalido(self):
        for ruim in ({"tipo": "cadastro_voz", "nome": "", "aprovado": True},
                     {"tipo": "cadastro_voz", "nome": "Bia", "aprovado": "sim"},
                     {"tipo": "cadastro_voz", "nome": "K" * 21, "aprovado": True}):
            with self.assertRaises(ValueError, msg=ruim):
                cc.texto_aviso(ruim, SAT["sat-0a1b2c"])

    def test_tipo_de_entrega_aprendizagem(self):
        self.assertEqual(cc.validar_pedido({"destino": "satelite:sat-0a1b2c", "texto": "x", "tipo": "aprendizagem"})[2],
                         "aprendizagem")

    def test_e_aviso_de_cadastro(self):
        self.assertTrue(cc.e_aviso_de_cadastro({"tipo": "cadastro_voz"}))
        self.assertFalse(cc.e_aviso_de_cadastro({"tipo": "timer_nao_atendido"}))
        self.assertFalse(cc.e_aviso_de_cadastro(None))


class TestCadastroComCache(unittest.TestCase):
    SAT = {"sat-0a1b2c": {"comodo": "escritorio", "senha": "x" * 40, "ip": "192.0.2.15"}}
    def setUp(self):
        d = tempfile.mkdtemp(); self.principal = os.path.join(d, "satellites.json"); self.cache = os.path.join(d, "cache.json")
    def test_principal_ok_atualiza_o_cache(self):
        json.dump(self.SAT, open(self.principal, "w"))
        self.assertEqual(cc.ler_satelites_com_cache(self.principal, self.cache), self.SAT)
        self.assertEqual(json.load(open(self.cache)), self.SAT)
        self.assertEqual(stat.S_IMODE(os.stat(self.cache).st_mode), 0o600)
    def test_principal_fora_usa_o_cache(self):
        json.dump(self.SAT, open(self.cache, "w"))
        self.assertEqual(cc.ler_satelites_com_cache(self.principal, self.cache), self.SAT)
    def test_sem_nenhum_devolve_vazio(self):
        self.assertEqual(cc.ler_satelites_com_cache(self.principal, self.cache), {})
    def test_principal_vazio_nao_apaga_o_cache(self):
        json.dump(self.SAT, open(self.cache, "w")); json.dump({}, open(self.principal, "w"))
        self.assertEqual(cc.ler_satelites_com_cache(self.principal, self.cache), self.SAT)


class TestMusicaDoAlarme(unittest.IsolatedAsyncioTestCase):
    """Spec 2026-10-21 R11: POST /satellite_media → script music_play no player do satélite."""
    class D:
        def __init__(self, sats, resposta=None, erro=None):
            self.satelites, self.chamadas, self._r, self._e = sats, [], resposta, erro

        async def play_music(self, player, query):
            self.chamadas.append((player, query))
            if self._e:
                raise self._e
            return self._r

    async def test_satellite_without_player_does_not_call_ha(self):
        d = self.D({"sat-1": {"comodo": "X"}})
        r = await cc.play_for_satellite("sat-1", "Legião Urbana", d)
        self.assertFalse(r["ok"])
        self.assertIn("erro", r)
        self.assertEqual(d.chamadas, [])

    async def test_satellite_with_player_calls_the_script_once(self):
        d = self.D({"sat-1": {"player": "media_player.x"}}, {"ok": True, "title": "Tempo Perdido"})
        r = await cc.play_for_satellite("sat-1", "Legião Urbana", d)
        self.assertEqual(r, {"ok": True, "title": "Tempo Perdido"})
        self.assertEqual(d.chamadas, [("media_player.x", "Legião Urbana")])

    async def test_script_not_ok_is_not_ok(self):
        d = self.D({"sat-1": {"player": "media_player.x"}}, {"ok": False, "title": None})
        self.assertFalse((await cc.play_for_satellite("sat-1", "x", d))["ok"])

    async def test_ha_error_is_not_ok_and_does_not_raise(self):
        d = self.D({"sat-1": {"player": "media_player.x"}}, erro=OSError("HA fora"))
        r = await cc.play_for_satellite("sat-1", "x", d)
        self.assertFalse(r["ok"])

    async def test_bad_query_is_refused_without_calling_ha(self):
        d = self.D({"sat-1": {"player": "media_player.x"}}, {"ok": True})
        for ruim in ("", "x" * 81, "a\nb", None, 3):
            r = await cc.play_for_satellite("sat-1", ruim, d)
            self.assertFalse(r["ok"], repr(ruim))
        self.assertEqual(d.chamadas, [])


if __name__ == "__main__":
    unittest.main()
