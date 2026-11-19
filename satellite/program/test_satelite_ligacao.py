"""Ligação do programa (friday_satelite.py) com hardware e rede simulados — spec 2026-10-12.
cd /homeassistant/tools/satelite/programa && python3 -m unittest test_satelite_ligacao -v"""
import shutil
import asyncio, json, os, sys, tempfile, time, types, unittest
from unittest import mock

# Módulos que só existem na Pi (ou que mexem em hardware) viram simulados SÓ durante o import do programa; depois
# os originais voltam, senão os outros testes da suíte (test_conexao, test_hardware) pegariam os simulados.
_SIMULADOS = ("numpy", "aiohttp", "openwakeword", "openwakeword.model", "openwakeword.vad", "hardware", "conexao",
              "porta_local", "friday_satelite", "whisper", "placa")
_ORIGINAIS = {n: sys.modules.get(n) for n in _SIMULADOS}
for nome in ("numpy", "aiohttp", "openwakeword", "openwakeword.model", "openwakeword.vad"):
    sys.modules.setdefault(nome, types.ModuleType(nome))
sys.modules["openwakeword.model"].Model = object
sys.modules["openwakeword.vad"].VAD = object


class Falsos:
    def __init__(self):
        self.escritas, self.sons, self.limpou, self.mensagens, self.conexoes = [], [], 0, [], []
        self.music, self.primed, self.mudo = [], False, []
        self.parou_ja, self.silenciadas, self.fechadas = 0, 0, 0
        self.luzes, self.cortes, self.falas_pessoa = [], 0, 0


F = Falsos()


class Mixer:
    def ler(self): return 4
    def escrever(self, k): F.escritas.append(k)


class MusicMixer:
    def __init__(self, card="USB", control="Music"): self.card = card
    def write(self, p): F.music.append(p)
    def prime(self): F.primed = True


class AltoFalante:
    ultimo_envio = None
    def tocar_wav(self, p): F.sons.append(os.path.basename(p))
    def tocar_pcm48(self, b): pass
    def limpar(self): F.limpou += 1
    def parar_ja(self): F.parou_ja += 1


class ControleMudo:
    def aplicar(self, mudo): F.mudo.append(mudo); return True


class Anel:
    def __init__(self, detector=None): pass
    def mostrar(self, nome, mudo=False): F.luzes.append((nome, mudo))


class Conexao:
    def __init__(self, endereco, dados, ao_evento, ao_audio):
        self.dados, self.enviados = dados, []
        F.conexoes.append(self)
    def enviar(self, pcm): self.enviados.append(pcm)
    def enviar_silencio(self, segundos=1.5): pass
    def enviar_mensagem(self, tipo, dados):
        F.mensagens.append((tipo, dados)); return True
    def silenciar(self): F.silenciadas += 1
    def cortar_fala(self): F.cortes += 1
    def pessoa_falou(self): F.falas_pessoa += 1
    async def abrir(self): pass
    async def fechar(self): F.fechadas += 1


hw = types.ModuleType("hardware")
hw.AltoFalante, hw.Anel, hw.Mixer, hw.MusicMixer = AltoFalante, Anel, Mixer, MusicMixer
hw.ControleMudo = ControleMudo
hw.Botao = hw.Captura = hw.CapturaDupla = hw.TeclasVolume = object
sys.modules["hardware"] = hw
cx = types.ModuleType("conexao")
cx.ConexaoFriday = Conexao
sys.modules["conexao"] = cx
wh = types.ModuleType("whisper")  # o real exige numpy; aqui só a assinatura (spec 2026-10-20)
wh.whisper_ratio_db = wh.noise_floor_rms = lambda *a, **k: None
sys.modules["whisper"] = wh
pl = types.ModuleType("porta_local")
sys.modules["porta_local"] = pl

sys.modules.pop("placa", None)  # a fresh one binds to the simulated hardware; the original comes back below
import friday_satelite as fs  # noqa: E402

for _n, _m in _ORIGINAIS.items():
    if _m is None:
        sys.modules.pop(_n, None)
    else:
        sys.modules[_n] = _m


class Base(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        global F
        F.__init__()
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d, True)
        self.p = mock.patch.multiple(fs, CFG=os.path.join(self.d, "config.json"), ID=os.path.join(self.d, "id"),
                                     TIMERS=os.path.join(self.d, "estado", "timers.json"), SONS=self.d)
        self.p.start()
        self.p_placa = mock.patch.object(fs.placa, "ler_placa", return_value="jabra")  # never the host's audio_board
        self.p_placa.start()
        import sons
        sons.gerar(self.d)
        # 17/11: os tons de fábrica são os da ElevenLabs; os gerados fazem o papel deles aqui
        for novo, gerado in (("inicio_elevenlabs.wav", "inicio_a.wav"), ("fim_elevenlabs_20261020.wav", "fim_a.wav"),
                             ("erro_b.wav", "erro_a.wav"), ("alarme_elevenlabs.wav", "alarme_a.wav"),
                             ("lembrete_elevenlabs.wav", "lembrete_a.wav")):
            if not os.path.exists(os.path.join(self.d, novo)):
                shutil.copy(os.path.join(self.d, gerado), os.path.join(self.d, novo))
        self.s = fs.Satelite(asyncio.get_running_loop())
        import cadastro
        self.s.vozes = cadastro.Vozes(os.path.join(self.d, "vozes"))  # never the Pi's /home/jose/vozes
        self.s.maestro.mudo = False  # a captura decidiria; aqui o microfone está liberado

    async def asyncTearDown(self):
        self.s._parar_alarme()
        self.p_placa.stop()
        self.p.stop()


class TestDucking(Base):
    async def test_start_primes_and_restores_full_music_volume(self):
        self.assertTrue(F.primed)
        self.assertEqual(F.music[-1], 100)

    async def test_music_ducks_while_conversation_is_open_and_returns_after(self):
        self.s.maestro.estado = "ouvindo"
        t = time.monotonic()
        for i in range(10):
            self.s.duck_tique(t + i * 0.05)
        self.assertEqual(F.music[-1], 15)
        self.s.maestro.estado = "parada"
        for i in range(10, 60):
            self.s.duck_tique(t + i * 0.05)
        self.assertEqual(F.music[-1], 100)

    async def test_hot_config_changes_duck_level(self):
        self.s.ao_mudar_config(dict(self.s.cfg, music_duck_percent=5))
        self.s.maestro.estado = "ouvindo"
        t = time.monotonic()
        for i in range(10):
            self.s.duck_tique(t + i * 0.05)
        self.assertEqual(F.music[-1], 5)


class TestMudoESonsTurno(Base):
    async def test_mudo_pela_porta_aplica_so_quando_muda(self):
        self.s.ao_mudar_config(dict(self.s.cfg, mudo=True))
        self.s.ao_mudar_config(dict(self.s.cfg, mudo=True))
        self.s.ao_mudar_config(dict(self.s.cfg, mudo=False))
        self.assertEqual(F.mudo, [True, False])

    async def test_arranque_restaura_o_ultimo_mudo_gravado(self):
        self.s.cfg["mudo"] = True
        self.s._aplicar_mudo(inicial=True)
        self.assertEqual(F.mudo, [True])

    async def test_arranque_sem_mudo_nao_escreve_na_captura(self):
        self.s.cfg["mudo"] = False
        self.s._aplicar_mudo(inicial=True)
        self.assertEqual(F.mudo, [])  # antes da Task 13 nada era escrito no arranque

    async def test_aplicar_mudo_fora_do_arranque_continua_escrevendo(self):
        self.s.cfg["mudo"] = False
        self.s._aplicar_mudo()
        self.assertEqual(F.mudo, [False])

    async def test_sons_turno_falso_pula_inicio_e_fim_mas_nao_o_erro(self):
        self.s.cfg["sons_turno"] = False
        for n in ("inicio", "fim", "erro"):
            self.s._agir("som", n)
        self.assertEqual(F.sons, ["erro_b.wav"])

    async def test_padrao_toca_os_sons_de_turno(self):
        self.s._agir("som", "inicio"); self.s._agir("som", "fim")
        self.assertEqual(F.sons, ["inicio_elevenlabs.wav", "fim_elevenlabs_20261020.wav"])


class EmissorFalso:
    def __init__(self): self.eventos = []
    def emitir(self, tipo, dados): self.eventos.append((tipo, dados))


class TestComandos(Base):
    async def test_mudanca_de_volume_pela_voz_emite_config_com_origem_voz(self):
        self.s.emissor = EmissorFalso()
        self.s._de_fora("comando", {"evento": "comando", "id": "c9", "acao": "volume", "numero": 7})
        self.assertIn(("config", {"mudancas": {"volume": 7}, "origem": "voz"}), self.s.emissor.eventos)

    async def test_volume_pela_conversa_escreve_toca_bip_e_responde_com_o_id(self):
        self.s.maestro.evento("palavra", 0.0)
        self.s._agir("conectar", None)
        self.s._de_fora("comando", {"evento": "comando", "id": "c9", "acao": "volume", "numero": 7})
        self.assertEqual(F.escritas[-1], 7)
        self.assertIn("volume_a.wav", F.sons)
        self.assertEqual(F.mensagens[-1][0], "resposta")
        self.assertEqual((F.mensagens[-1][1]["id"], F.mensagens[-1][1]["posicao"]), ("c9", 7))

    async def test_update_pending_pela_porta_liga_o_anel_e_aparece_no_estado(self):
        self.assertFalse(self.s.estado_publico()["update_pendente"])
        r = self.s.comando_porta({"acao": "update_pending", "ligado": True})
        self.assertEqual((r["ok"], r["ligado"]), (True, True))
        self.assertTrue(self.s.anel.pendente)
        self.assertTrue(self.s.estado_publico()["update_pendente"])
        self.s.comando_porta({"acao": "update_pending", "ligado": False})
        self.assertFalse(self.s.estado_publico()["update_pendente"])
        self.assertFalse(self.s.comando_porta({"acao": "update_pending", "ligado": "x"})["ok"])

    async def test_status_publishes_the_capture_rate(self):
        """Task 5 (xvf3800-support): `captura_taxa` (3 casas) e `captura_idade_s` (monotônico, revisão I5) no /status; None até a 1ª janela."""
        e = self.s.estado_publico()
        self.assertIsNone(e["captura_taxa"])
        self.assertIsNone(e["captura_idade_s"])
        t0 = 100.0
        for i in range(70):  # 11,2 s a tempo real, pedaços de 2560 amostras
            self.s.taxa_captura.alimentar(2560, t0 + i * 0.16)
        e = self.s.estado_publico()
        self.assertEqual(e["captura_taxa"], round(self.s.taxa_captura.atual(), 3))
        self.assertAlmostEqual(e["captura_taxa"], 1.0, delta=0.01)
        self.assertIsInstance(e["captura_idade_s"], float)

    async def test_estado_publico_lista_as_palavras_carregadas(self):
        # a saúde do OTA (friday-ota-apply) exige palavra carregada: lista vazia até a captura criar o modelo
        self.assertEqual(self.s.estado_publico()["palavras_carregadas"], [])

        class Modelo:
            models = {"sexta_feira": 1, "friday": 1}
        self.s._modelo_palavra = Modelo()
        self.assertEqual(self.s.estado_publico()["palavras_carregadas"], ["friday", "sexta_feira"])

    async def test_timer_criado_vai_para_o_arquivo(self):
        self.s._agir("conectar", None)
        self.s._de_fora("comando", {"id": "c1", "acao": "timer_criar", "duracao_s": 600, "nome": "arroz"})
        with open(fs.TIMERS) as f:
            self.assertEqual(json.load(f)[0]["nome"], "arroz")


class TestTimers(Base):
    async def test_timer_vencido_toca_alarme_no_volume_de_alertas(self):
        self.s.timers.criar(1, "ovo", time.time() - 5)
        self.s.tique()
        self.assertEqual(self.s.maestro.estado, "alarme")
        self.assertEqual(self.s.volume.atual, 6)  # volume_alertas padrão
        await asyncio.sleep(0.05)
        self.assertIn("alarme_elevenlabs.wav", F.sons)
        self.s.evento("botao")
        self.assertEqual(self.s.volume.atual, self.s.volume.normal)
        self.assertGreaterEqual(F.limpou, 1)

    async def test_conversa_do_timer_vai_sem_pre_gravacao_e_com_o_timer(self):
        self.s.recentes.append(b"\x01" * 2560)
        self.s._agir("conectar", {"timer": {"id": "t1", "nome": "ovo", "duracao_s": 60}})
        c = F.conexoes[-1]
        self.assertEqual(c.dados["timer"]["nome"], "ovo")
        self.assertEqual(c.enviados, [])

    async def test_conversa_pela_palavra_leva_o_nome_e_a_pre_gravacao(self):  # 28/10
        self.s.recentes.append(b"\x01" * 2560)
        self.s._agir("conectar", {"wake_word": "sexta_feira"})
        c = F.conexoes[-1]
        self.assertEqual(c.dados["wake_word"], "sexta_feira")
        self.assertNotEqual(c.enviados, [])

    async def test_timers_da_pi_desligada_voltam(self):
        os.makedirs(os.path.dirname(fs.TIMERS), exist_ok=True)
        agora = time.time()
        with open(fs.TIMERS, "w") as f:
            json.dump([{"id": "a", "nome": "recente", "duracao_s": 60, "termino": agora - 60},
                       {"id": "b", "nome": "velho", "duracao_s": 60, "termino": agora - 3600},
                       {"id": "c", "nome": "ativo", "duracao_s": 60, "termino": agora + 600}], f)
        self.s.carregar_timers()
        self.assertEqual(self.s.maestro.estado, "alarme")
        self.assertEqual([t["nome"] for t in self.s.estado_publico()["timers"]], ["ativo"])

    async def test_nao_atendido_avisa_o_add_on(self):
        avisos = []

        async def falso(t):
            avisos.append(t)
        self.s._avisar_nao_atendido = falso
        self.s._agir("aviso_nao_atendido", {"id": "t1"})
        await asyncio.sleep(0)
        self.assertEqual(avisos, [{"id": "t1"}])


class TestCampainha(Base):
    async def test_campainha_abre_entrega_no_volume_de_alertas(self):
        self.assertEqual(self.s.campainha("abcd1234"), "vou_abrir")
        c = F.conexoes[-1]
        self.assertEqual(c.dados["entrega"], "abcd1234")
        self.assertIn("lembrete_elevenlabs.wav", F.sons)
        self.assertTrue(self.s.volume.em_alerta)

    async def test_botao_na_entrega_avisa_o_bot(self):
        self.s.campainha("abcd1234")
        self.s.evento("conectou")
        self.s.evento("botao")
        self.assertIn(("atendida", {}), F.mensagens)


class TestVolumeFisico(Base):
    async def test_botao_mais_do_jabra_sincroniza_e_grava(self):
        self.s.volume.iniciar()
        self.s.apertou_volume(+1)
        self.assertEqual(F.escritas[-1], 5)
        self.s.volume.tique(time.monotonic() + 10)
        with open(fs.CFG) as f:
            self.assertEqual(json.load(f)["volume"], 5)

    async def test_put_config_muda_volume_e_alertas(self):
        novo = dict(self.s.cfg, volume=9, volume_alertas=8)
        self.s.ao_mudar_config(novo)
        self.assertEqual((F.escritas[-1], self.s.volume.alertas_numero), (9, 8))


class TestAlarmeMusical(Base):
    TM = {"id": "t9", "nome": "acordar", "duracao_s": 60, "termino": 0.0, "music": "Legião Urbana"}

    async def test_request_music_ok_ends_alarm_with_music(self):
        pedidos = []

        async def post(query):
            pedidos.append(query)
            return {"ok": True, "title": "Tempo Perdido"}
        self.s._post_musica = post
        self.s.evento("timer_venceu", self.TM)
        await asyncio.sleep(0.05)
        self.assertEqual(pedidos, ["Legião Urbana"])
        self.assertEqual(self.s.maestro.estado, "parada")

    async def test_request_music_failure_keeps_alarm_and_falls_back(self):
        async def post(query):
            raise OSError("VM fora")
        self.s._post_musica = post
        self.s.evento("timer_venceu", self.TM)
        await asyncio.sleep(0.05)
        self.assertEqual(self.s.maestro.estado, "alarme")
        self.assertFalse(self.s.maestro.musical_alarm)

    async def test_request_music_not_ok_falls_back(self):
        async def post(query):
            return {"ok": False}
        self.s._post_musica = post
        self.s.evento("timer_venceu", self.TM)
        await asyncio.sleep(0.05)
        self.assertEqual(self.s.maestro.estado, "alarme")
        self.assertFalse(self.s.maestro.musical_alarm)

    async def test_alarm_ramp_hot_change(self):
        self.s.cfg = dict(self.s.cfg, alarm_ramp_s=0)
        self.s.evento("timer_venceu", {"id": "a", "nome": None, "duracao_s": 5, "termino": 0.0})
        self.assertEqual(F.escritas[-1], 6)  # sem rampa: direto no volume de alertas
        self.s.evento("botao")
        F.escritas.clear()
        self.s.ao_mudar_config(dict(self.s.cfg, alarm_ramp_s=30))  # PUT /config, sem reinício
        self.s.evento("timer_venceu", {"id": "b", "nome": None, "duracao_s": 5, "termino": 0.0})
        self.assertEqual(F.escritas[-1], 1)  # agora o alarme começa na posição 1

    async def test_music_ramp_starts_only_when_configured(self):
        self.s.cfg = dict(self.s.cfg, alarm_ramp_s=0)
        n = len(F.music)
        self.s._agir("music_ramp", None)
        self.assertEqual(len(F.music), n)
        self.s.cfg = dict(self.s.cfg, alarm_ramp_s=30)
        self.s._agir("music_ramp", None)
        self.assertEqual(F.music[-1], 10)

class TestCancelarConversa(Base):
    async def test_cancelar_conversa_para_audio_avisa_e_fecha(self):
        # spec 2026-11-04: o vermelho para o som na hora, avisa o bot, silencia e fecha a conexão após 0,3 s
        class EmissorFalso:
            def __init__(self): self.eventos = []
            def emitir(self, tipo, dados): self.eventos.append((tipo, dados))
        self.s.emissor = EmissorFalso()
        self.s.evento("palavra")
        conexao = F.conexoes[-1]
        self.s.evento("botao", "vermelho")
        self.assertEqual(F.parou_ja, 1)
        self.assertEqual(F.silenciadas, 1)
        self.assertIn(("cancelar", {}), F.mensagens)
        self.assertIsNone(self.s.conexao)
        self.assertFalse(self.s.microfone_aberto)
        self.assertEqual(self.s.maestro.estado, "parada")
        self.assertEqual(F.sons[-1], "fim_elevenlabs_20261020.wav")  # os sons pedidos DEPOIS do cancelamento tocam normalmente
        self.assertEqual(F.fechadas, 0)  # ainda não: a mensagem precisa sair
        await asyncio.sleep(0.45)
        self.assertEqual(F.fechadas, 1)
        fechadas = [e for e in self.s.emissor.eventos if e == ("conversa", {"estado": "fechada"})]
        self.assertEqual(len(fechadas), 1)
        self.assertIsNotNone(conexao)

    async def test_vermelho_sem_conexao_aberta_nao_levanta(self):
        self.s.conexao = None
        self.s._agir("cancelar_conversa", None)   # Review Focus 2
        self.s._agir("desconectar", None)
        self.assertEqual(F.parou_ja, 1)


class TestContinuacaoMusicaEPausa(Base):
    async def test_musica_agora_falha_vira_false(self):
        async def falha(*a, **k):
            raise FileNotFoundError("sudo")
        with mock.patch.object(asyncio, "create_subprocess_exec", falha):
            self.assertIs(await self.s._musica_agora(), False)

        async def pendura(*a, **k):
            class P:
                returncode = None
                async def communicate(self_):
                    await asyncio.sleep(30)
                def kill(self_): pass
                async def wait(self_): return 0
            return P()
        self.s.sendspin_timeout_s = 0.1
        t0 = time.monotonic()
        with mock.patch.object(asyncio, "create_subprocess_exec", pendura):
            self.assertIs(await self.s._musica_agora(), False)
        self.assertLess(time.monotonic() - t0, 1.5)

    async def test_musica_agora_true_com_state_playing(self):
        async def toca(*a, **k):
            class P:
                returncode = 0
                async def communicate(self_): return b"state: playing\n", b""
            return P()
        with mock.patch.object(asyncio, "create_subprocess_exec", toca):
            self.assertIs(await self.s._musica_agora(), True)

    async def test_ela_comecou_agenda_consulta_de_musica(self):
        async def sim(): return True
        self.s._musica_agora = sim
        self.s.evento("palavra")
        self.s.evento("conectou")
        self.s.evento("ela_comecou")
        await asyncio.sleep(0.05)
        self.assertIs(self.s.maestro._musica_tocando, True)

    async def test_pausa_de_fim_de_fala_a_quente(self):
        self.assertEqual(fs.para_apos_de({}), 10)
        self.assertEqual(fs.para_apos_de({"end_of_speech_pause_s": 1.2}), 15)
        self.assertEqual(fs.para_apos_de({"end_of_speech_pause_s": 0.5}), 6)


class TestAnel(Base):
    async def test_luz_do_maestro_chega_ao_anel_como_estado(self):
        vistos = []
        self.s.anel = types.SimpleNamespace(show_maestro=lambda estado, direcao=None: vistos.append(estado))
        for nome in ("ouvindo", "cerebro", "nao_perturbe", "alarme", "parada"):
            self.s._agir("luz", nome)
        self.assertEqual(vistos, ["listen", "think", "dnd", "alarm", "off"])

    async def test_sem_xvf_o_anel_e_o_jabra_com_os_nomes_de_hoje(self):
        import ring
        self.assertIsInstance(self.s.anel, ring.JabraRing)

class TestProvisionamento(Base):
    async def test_ring_pela_porta_chega_ao_anel(self):
        vistos = []
        self.s.anel = types.SimpleNamespace(show=lambda estado, direcao=None: vistos.append(estado))
        r = self.s.comando_porta({"acao": "ring", "state": "setup"})
        self.assertTrue(r["ok"])
        self.assertEqual(vistos, ["setup"])

    async def test_play_phrases_toca_os_arquivos_que_existem(self):
        os.makedirs(os.path.join(self.d, "onboarding"))
        with open(os.path.join(self.d, "onboarding", "ready.wav"), "wb") as f:
            f.write(b"x")
        r = self.s.comando_porta({"acao": "play_phrases", "keys": ["ready", "nada"]})
        self.assertEqual(r["played"], 1)
        self.assertIn("ready.wav", F.sons)

    async def test_teste_da_palavra_ponta_a_ponta(self):
        emitidos = []
        self.s.emissor.emitir = lambda tipo, dados: emitidos.append((tipo, dados))
        r = self.s.comando_porta({"acao": "wake_word_test", "timeout_s": 30})
        self.assertTrue(r["ok"])
        self.assertEqual(self.s.maestro.estado, "teste_palavra")
        self.s.evento("palavra", {"wake_word": "friday"})
        self.s.evento("palavra", {"wake_word": "friday"})
        self.assertEqual(emitidos, [("onboarding", {"resultado": "wake_word_ok"})])
        self.assertEqual(self.s.maestro.estado, "parada")

    async def test_apresentacao_abre_conversa_com_presentation(self):
        fatos = {"areas": [{"id": "sala", "name": "Sala"}]}
        r = self.s.comando_porta({"acao": "open_presentation", "facts": fatos})
        self.assertTrue(r["ok"])
        self.assertEqual(F.conexoes[-1].dados["presentation"], fatos)

    async def test_apresentacao_recusada_com_mudo(self):
        self.s.maestro.mudo = True
        r = self.s.comando_porta({"acao": "open_presentation", "facts": {}})
        self.assertFalse(r["ok"])

    async def test_set_area_emite_evento(self):
        emitidos = []
        self.s.emissor.emitir = lambda tipo, dados: emitidos.append((tipo, dados))
        self.s.comando_porta({"acao": "set_area", "area_id": "sala"})
        self.assertEqual(emitidos, [("onboarding", {"resultado": "area_selected", "area_id": "sala"})])



class TestCadastroContaComoConversa(Base):
    """05/11: o HA só diz "pronta" quando a conversa fecha; a gravação de voz que vem logo depois faz parte dela."""
    async def test_gravacao_abre_e_cancelamento_fecha(self):
        self.s.emissor = EmissorFalso()
        self.s._agendado = {"nome": "Ana", "id": "ana"}
        self.s._agir("cadastro", "iniciar")
        self.assertEqual(self.s.emissor.eventos[-1], ("conversa", {"estado": "aberta"}))
        self.s._agir("cadastro", "cancelar")
        self.assertEqual([e for e in self.s.emissor.eventos if e[0] == "conversa"],
                         [("conversa", {"estado": "aberta"}), ("conversa", {"estado": "fechada"})])

    async def test_cancelar_sem_gravacao_nao_emite(self):
        self.s.emissor = EmissorFalso()
        self.s._agir("cadastro", "interromper")
        self.assertEqual([e for e in self.s.emissor.eventos if e[0] == "conversa"], [])


class TestRedeEsgotadaLigacao(unittest.TestCase):
    """Spec 2026-11-08: o netwatch (processo separado) grava o estado; o programa principal só o lê e vira evento.
    Verificações de texto no fonte (o ensaio de verdade é no Pi)."""
    @classmethod
    def setUpClass(cls):
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "friday_satelite.py"), encoding="utf-8") as f:
            cls.src = f.read()

    def test_imports_net_recovery_at_top(self):
        self.assertIn("\nimport net_recovery\n", self.src)
        self.assertLess(self.src.index("import net_recovery"), self.src.index("\nclass "))

    def test_tique_polls_the_state_file_at_most_every_5_s(self):
        corpo = self.src.split("    def tique(self):", 1)[1].split("\n    def ", 1)[0]
        self.assertIn("net_recovery.read_exhausted(", corpo)
        self.assertIn("time.monotonic()", corpo)
        self.assertIn(">= 5", corpo)
        self.assertIn('self.evento("rede_esgotada"', corpo)
        self.assertIn('"net_exhausted"', corpo)
        self.assertIn("self.maestro.rede_esgotada", corpo)

    def test_agir_handles_desfazer_mudo(self):
        corpo = self.src.split("    def _agir(self, tipo, arg):", 1)[1].split("\n    def ", 1)[0]
        self.assertIn('tipo == "desfazer_mudo"', corpo)
        self.assertIn("ring.ESTADO_DO_MAESTRO[self.maestro._luz_do_estado()]", corpo)
        self.assertIn('"undo_mute"', corpo)
        self.assertIn("\"undo_mute\": \"xvf3800\"", corpo)  # 17/11: o pino do XVF desfaz o mudo


if __name__ == "__main__":
    unittest.main()


class TestAnelComMudo(Base):
    """v0.3.7 (16/11): mudo + não perturbe gravados; depois do restart a luz era só a do não perturbe."""
    async def test_restart_with_mute_and_dnd_saved_draws_mute_first(self):
        self.s.cfg["mudo"], self.s.cfg["nao_perturbe"] = True, True
        F.luzes.clear()
        self.s.arranque_do_anel()
        self.s.evento("nao_perturbe_inicial", True)
        self.assertTrue(F.luzes and all(m for _, m in F.luzes), F.luzes)
        self.assertEqual(F.luzes[-1], ("nao_perturbe", True))

    async def test_unmute_from_ha_clears_indicator(self):
        self.s.cfg["mudo"] = True
        self.s.arranque_do_anel()
        novo = dict(self.s.cfg, mudo=False)
        self.s.ao_mudar_config(novo)
        self.assertEqual(F.luzes[-1][1], False)

    async def test_button_mute_leaves_the_jabra_alone(self):
        # o mudo do botão é do próprio Jabra (vermelho dele); escrever uma luz sem o bit o desfaria (06/10)
        self.s.cfg["mudo"] = False
        self.s.arranque_do_anel()
        n = len(F.luzes)
        self.s.evento("mudo", True)
        self.assertTrue(all(not m for _, m in F.luzes[n:]))
        self.assertEqual(F.luzes[n:], [])

    async def test_unmute_from_ha_after_the_zeros_set_maestro_mudo(self):  # C1
        self.s.cfg["mudo"] = True
        self.s.arranque_do_anel()
        self.s.evento("mudo", True)  # o bit cortou o microfone: a captura vê zeros
        self.s.ao_mudar_config(dict(self.s.cfg, mudo=False))
        self.assertEqual(F.luzes[-1][1], False)  # a luz sem o bit sai: o Jabra libera o microfone

    async def test_desfazer_mudo_writes_without_the_bit(self):  # I1
        self.s.cfg["mudo"] = False
        self.s.arranque_do_anel()
        self.s.maestro.mudo = True
        self.s._agir("desfazer_mudo", None)
        self.assertEqual(F.luzes[-1][1], False)

    async def test_preview_command_is_unitary(self):
        self.s.cfg["mudo"] = True
        self.s.arranque_do_anel()
        self.s.comando_porta({"acao": "ring", "state": "alarm", "preview": True})
        self.assertEqual(F.luzes[-1], ("alarme", True))  # um estado por vez, mas no Jabra o mudo não se perde
        self.s.comando_porta({"acao": "ring", "state": None, "preview": True})
        self.assertEqual(F.luzes[-1][1], True)


class DisputaFalsa:
    """ClienteDisputa de mentira (spec 2026-11-16): `liberar` segura o veredito até o teste soltar."""
    def __init__(self, outros=0, veredito="go"):
        self.outros, self.veredito = outros, veredito
        self.claims, self.registros, self.antes = [], [], None
        self.liberar = asyncio.Event()
    def precisa_disputar(self): return self.outros > 0
    async def disputar(self, claim):
        self.claims.append(claim)
        if self.antes:
            self.antes()
        await self.liberar.wait()
        return {"verdict": self.veredito, "reason": "teste"}
    async def registrar(self, claim): self.registros.append(claim)
    async def fechar(self): pass


class TestDisputa(Base):
    PALAVRA = {"whisper": False, "wake_word": "friday", "score": 0.83, "voice_dbfs": -31.5}

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.luzes = []
        self.s.anel.show_maestro = lambda estado, direcao=None: self.luzes.append(estado)

    def disputa(self, **k):
        self.s.disputa = DisputaFalsa(**k)
        return self.s.disputa

    async def solta(self, d):
        await asyncio.sleep(0)
        d.liberar.set()
        await asyncio.sleep(0.01)

    async def test_alone_acts_now_and_records(self):
        d = self.disputa(outros=0)
        self.s.evento("palavra", self.PALAVRA)
        self.assertEqual(F.sons, ["inicio_elevenlabs.wav"])  # na hora, sem esperar ninguém
        await asyncio.sleep(0.01)
        self.assertEqual(d.claims, [])
        self.assertEqual(len(d.registros), 1)
        c = d.registros[0]
        self.assertEqual((c["wake_word"], c["score"], c["voice_dbfs"], c["busy"]), ("friday", 0.83, -31.5, False))
        self.assertIsInstance(c["claim_id"], str)
        self.assertEqual(F.conexoes[-1].dados["claim_id"], c["claim_id"])

    async def test_claim_go_then_acts(self):
        d = self.disputa(outros=1)
        self.s.evento("palavra", self.PALAVRA)
        await asyncio.sleep(0)
        self.assertEqual(self.luzes, ["listen"])  # a luz de escuta acende já
        self.assertEqual((F.sons, F.conexoes), ([], []))  # som e conversa só depois do veredito
        self.assertEqual(self.s.maestro.estado, "parada")
        await self.solta(d)
        self.assertEqual(F.sons, ["inicio_elevenlabs.wav"])
        self.assertEqual(self.s.maestro.estado, "conectando")
        self.assertEqual(F.conexoes[-1].dados["claim_id"], d.claims[0]["claim_id"])
        self.assertEqual(d.claims[0]["wake_word"], "friday")
        self.assertFalse(d.claims[0]["busy"])
        self.assertEqual(d.registros, [])

    async def test_claim_lost_in_parada_restores_light(self):
        d = self.disputa(outros=1, veredito="lost")
        self.s.evento("palavra", self.PALAVRA)
        await self.solta(d)
        self.assertEqual((F.sons, F.conexoes), ([], []))
        self.assertEqual(self.s.maestro.estado, "parada")
        self.assertEqual(self.luzes, ["listen", "off"])  # volta à luz do estado (parada)

    async def test_claim_lost_while_busy_keeps_conversation(self):
        d = self.disputa(outros=1, veredito="lost")
        m = self.s.maestro
        m.estado, m._abertura = "aguardando", {"inicio": 0.0, "como_abriu": "palavra"}
        vistos = []
        original = m.evento
        m.evento = lambda nome, agora, valor=None: (vistos.append(nome), original(nome, agora, valor))[1]
        self.s.evento("palavra", self.PALAVRA)
        await self.solta(d)
        self.assertTrue(d.claims[0]["busy"])
        self.assertNotIn("palavra", vistos)
        self.assertEqual(m.estado, "aguardando")
        self.assertEqual((F.sons, F.conexoes, self.luzes), ([], [], []))  # a luz do ocupado não muda

    async def test_claim_go_while_busy_acts_as_today(self):
        d = self.disputa(outros=1, veredito="go")
        m = self.s.maestro
        m.estado, m._abertura = "aguardando", {"inicio": 0.0, "como_abriu": "palavra"}
        self.s.evento("palavra", self.PALAVRA)
        await self.solta(d)
        self.assertEqual(m.estado, "ouvindo")  # reabre como hoje
        self.assertEqual(F.sons, ["inicio_elevenlabs.wav"])

    async def test_claim_during_a_pending_delivery_like_any_busy_state(self):
        d = self.disputa(outros=1, veredito="go")
        m = self.s.maestro
        m.estado, m._abertura, m._recado = "ouvindo", {"inicio": 0.0, "como_abriu": "entrega"}, True
        self.s.evento("palavra", self.PALAVRA)
        await self.solta(d)
        self.assertTrue(d.claims[0]["busy"])
        self.assertEqual(F.parou_ja, 1)  # b287504: só a palavra ativa corta o recado

    async def test_no_claim_when_maestro_would_ignore(self):
        d = self.disputa(outros=1)
        casos = [("muda", lambda m: setattr(m, "mudo", True)),
                 ("conversa aberta sem recado", lambda m: (setattr(m, "estado", "ouvindo"),
                                                          setattr(m, "_abertura", {"inicio": 0.0, "como_abriu": "palavra"}))),
                 ("teste da palavra", lambda m: (setattr(m, "estado", "teste_palavra"), setattr(m, "_teste", {"contagem": 0}))),
                 ("cadastro", lambda m: (setattr(m, "estado", "cadastro"), setattr(m, "_cadastro", {"nome": "x"})))]
        for nome, prepara in casos:
            with self.subTest(nome):
                self.s.maestro.mudo = False
                self.s.maestro.estado, self.s.maestro._abertura, self.s.maestro._cadastro = "parada", None, None
                prepara(self.s.maestro)
                self.s.evento("palavra", self.PALAVRA)
                await asyncio.sleep(0.01)
                self.assertEqual((d.claims, d.registros, self.luzes), ([], [], []))
                self.assertFalse(self.s._claim_pendente)

    async def test_alone_and_muted_does_not_record_either(self):
        d = self.disputa(outros=0)
        self.s.maestro.mudo = True
        self.s.evento("palavra", self.PALAVRA)
        await asyncio.sleep(0.01)
        self.assertEqual((d.claims, d.registros), ([], []))

    async def test_button_and_timer_never_claim(self):
        d = self.disputa(outros=1)
        self.s.evento("botao", None)
        self.assertEqual(self.s.maestro.estado, "conectando")
        self.s.evento("botao", "vermelho")
        self.s.evento("timer_venceu", TestAlarmeMusical.TM)
        self.s.campainha("abcd1234")
        await asyncio.sleep(0.01)
        self.assertEqual((d.claims, d.registros), ([], []))

    async def test_second_word_while_pending_is_ignored(self):
        d = self.disputa(outros=1)
        self.s.evento("palavra", self.PALAVRA)
        self.s.evento("palavra", dict(self.PALAVRA, score=0.9))
        await self.solta(d)
        self.assertEqual(len(d.claims), 1)
        self.assertEqual(len(F.conexoes), 1)
        self.assertFalse(self.s._claim_pendente)

    async def test_word_without_measurements_acts_as_today(self):
        d = self.disputa(outros=1)
        self.s.evento("palavra", {"whisper": False, "wake_word": "friday"})
        self.assertEqual(F.sons, ["inicio_elevenlabs.wav"])
        self.assertEqual((d.claims, d.registros), ([], []))

    async def test_preroll_covers_wait(self):
        self.s.recentes.extend(bytes([i]) * 2 for i in range(29))
        self.s._espera_disputa_s = 0.3  # a espera medida do veredito (injetada, sem relógio)
        self.s._agir("conectar", {"wake_word": "friday"})
        enviados = [b[0] for b in F.conexoes[-1].enviados]
        self.assertEqual(len(enviados), int(0.4 / 0.08) + 4 + 4)
        self.assertEqual(enviados, list(range(29 - 13, 29)))  # os mais novos, contíguos até o microfone ao vivo
        self.assertEqual(self.s._espera_disputa_s, 0.3)  # quem zera é `_disputar_e_agir`

    async def test_preroll_with_wait_measured_by_the_claim_resets(self):
        d = self.disputa(outros=1)
        self.s.recentes.extend(bytes([i]) * 2 for i in range(29))
        self.s.evento("palavra", self.PALAVRA)
        await self.solta(d)
        self.assertGreaterEqual(len(F.conexoes[-1].enviados), int(0.4 / 0.08) + 4)  # pelo menos o de sempre
        self.assertEqual(self.s._espera_disputa_s, 0)

    async def test_preroll_unchanged_when_alone(self):
        self.disputa(outros=0)
        self.s.recentes.extend(bytes([i]) * 2 for i in range(29))
        self.s.evento("palavra", self.PALAVRA)
        self.assertEqual(len(F.conexoes[-1].enviados), int(0.4 / 0.08) + 4)

    async def test_second_word_without_measurements_hits_the_pending_guard(self):
        d = self.disputa(outros=1)
        self.s.evento("palavra", self.PALAVRA)
        self.s.evento("palavra", {"whisper": False, "wake_word": "friday"})
        self.s.evento("palavra")
        self.assertEqual(F.sons, [])  # nada despachado enquanto o veredito não vem
        await self.solta(d)
        self.assertEqual(len(F.conexoes), 1)

    async def test_doorbell_during_the_wait_queues_and_the_word_goes_first(self):
        d = self.disputa(outros=1, veredito="go")
        self.s.evento("palavra", self.PALAVRA)
        await asyncio.sleep(0)
        self.assertEqual(self.s.campainha("abcd1234"), "na_fila")
        self.s.tique()  # o tique não abre a fila por cima do aviso
        self.assertEqual(F.conexoes, [])
        await self.solta(d)
        self.assertEqual(F.parou_ja, 0)  # a entrega não foi cortada
        self.assertNotIn("entrega", F.conexoes[-1].dados)
        self.assertEqual(F.conexoes[-1].dados["wake_word"], "friday")
        self.assertEqual(self.s.maestro.publico()["fila_entregas"], 1)

    async def test_timer_during_the_wait_wins_and_the_word_is_stale(self):
        d = self.disputa(outros=1, veredito="go")
        self.s.evento("palavra", self.PALAVRA)
        await asyncio.sleep(0)
        self.s.evento("timer_venceu", TestAlarmeMusical.TM)
        self.assertEqual(self.s.maestro.estado, "alarme")
        await self.solta(d)
        self.assertEqual(self.s.maestro.estado, "alarme")  # o alarme não foi encerrado por uma palavra anterior a ele
        self.assertEqual(F.conexoes, [])
        self.assertFalse(self.s._claim_pendente)

    async def test_mute_during_the_wait_drops_the_word_and_never_touches_a_muted_ring(self):
        d = self.disputa(outros=1, veredito="go")
        self.s.evento("palavra", self.PALAVRA)
        await asyncio.sleep(0)
        self.s.evento("mudo", True)
        await self.solta(d)
        self.assertEqual((F.sons, F.conexoes), ([], []))
        self.assertEqual(self.luzes, ["listen"])  # mudo do botão: a luz é do Jabra, nada é escrito por cima
        self.s.evento("mudo", False)  # ao desmutar o maestro refaz a luz
        self.assertEqual(self.luzes[-1], "off")

    async def test_software_mute_during_the_wait_restores_the_light(self):
        d = self.disputa(outros=1, veredito="go")
        self.s.maestro.mudo_software = True
        self.s.evento("palavra", self.PALAVRA)
        await asyncio.sleep(0)
        self.s.evento("mudo", True)
        await self.solta(d)
        self.assertEqual(self.luzes, ["listen", "off"])

    async def test_conversation_closed_during_the_wait_still_answers_the_word(self):
        """Final review 16/11 (item 3): the conversation closing by itself (AGUARDANDO/CORTADA/open -> PARADA) during the
        <=0.3 s wait must not swallow a won word: it opens a new conversation."""
        for antes in ("aguardando", "cortada", "ouvindo"):
            with self.subTest(antes=antes):
                F.sons.clear(); F.conexoes.clear()
                d = self.disputa(outros=1, veredito="go")
                m = self.s.maestro
                m.estado, m._abertura = antes, {"inicio": 0.0, "como_abriu": "palavra"}
                m._recado = antes == "ouvindo"  # an open conversation only claims over a pending message
                self.s.evento("palavra", self.PALAVRA)
                await asyncio.sleep(0)
                self.assertTrue(self.s._claim_pendente)
                m.estado, m._abertura, m._recado = "parada", None, False
                await self.solta(d)
                self.assertEqual(m.estado, "conectando")
                self.assertEqual(F.conexoes[-1].dados["claim_id"], d.claims[0]["claim_id"])
                m.estado, m._abertura = "parada", None

    async def test_button_closing_the_conversation_during_the_wait_keeps_the_word_stale(self):
        d = self.disputa(outros=1, veredito="go")
        m = self.s.maestro
        m.estado, m._abertura = "aguardando", {"inicio": 0.0, "como_abriu": "palavra"}
        self.s.evento("palavra", self.PALAVRA)
        await asyncio.sleep(0)
        self.s.evento("botao", "vermelho")  # the person cancelled: the word from before must not reopen
        self.assertEqual(m.estado, "parada")
        n = len(F.conexoes)
        await self.solta(d)
        self.assertEqual(len(F.conexoes), n)
        self.assertEqual(m.estado, "parada")

    async def test_claim_lost_in_alarm_keeps_the_alarm_ringing(self):
        """Owner decision 16/11: the loser acts as if it never heard the word, in every state: the alarm keeps ringing."""
        d = self.disputa(outros=1, veredito="lost")
        self.s.evento("timer_venceu", {"id": "t1", "nome": "ovo", "duracao_s": 60, "termino": 0.0})
        self.assertEqual(self.s.maestro.estado, "alarme")
        self.s.evento("palavra", self.PALAVRA)
        await asyncio.sleep(0)
        self.assertEqual(self.s.maestro.estado, "alarme")
        self.assertTrue(d.claims[0]["busy"])
        antes = list(F.sons)
        await self.solta(d)
        self.assertEqual(self.s.maestro.estado, "alarme")  # still ringing, nothing ended it
        self.assertEqual(F.conexoes, [])
        self.assertEqual(F.sons, antes)
        self.assertFalse(self.s._claim_pendente)

    async def test_claim_lost_after_the_alarm_ended_during_the_wait_only_restores_the_light(self):
        d = self.disputa(outros=1, veredito="lost")
        self.s.evento("timer_venceu", {"id": "t1", "nome": "ovo", "duracao_s": 60, "termino": 0.0})
        self.s.evento("palavra", self.PALAVRA)
        await asyncio.sleep(0)
        self.s.evento("botao")  # the button stopped it during the wait
        self.assertEqual(self.s.maestro.estado, "parada")
        await self.solta(d)
        self.assertEqual((self.s.maestro.estado, F.conexoes), ("parada", []))

    async def test_whisper_and_recordings_use_the_2_s_window_not_the_preroll_buffer(self):
        import inspect, re
        self.s.recentes.extend(bytes([i]) * 2 for i in range(29))
        janela = self.s._janela_palavra()
        self.assertEqual(len(janela), fs.PEDACOS_JANELA_PALAVRA)
        self.assertEqual(fs.PEDACOS_JANELA_PALAVRA, 25)
        self.assertEqual(janela[-1], bytes([28]) * 2)
        fonte = inspect.getsource(fs.Satelite.captura)
        usos = [l.strip() for l in fonte.splitlines() if "self.recentes" in l]
        self.assertEqual(usos, ["self.recentes.append(pcm)"])  # tudo mais passa por _janela_palavra()
        self.assertIn('b"".join(self._janela_palavra())', fonte)  # entrada do sussurro e do piso de ruído

    async def test_active_word_value_carries_score_and_voice_level(self):
        import array
        pcm = array.array("h", [1000] * 1280).tobytes()
        self.s.recentes.extend([pcm] * 20)
        v = self.s._valor_palavra(False, "friday", 0.8, 0.55)
        self.assertEqual((v["score"], v["verifier_score"], v["wake_word"], v["whisper"]), (0.8, 0.55, "friday", False))
        self.assertAlmostEqual(v["voice_dbfs"], -30.3, places=1)
        self.assertNotIn("verifier_score", self.s._valor_palavra(False, "friday", 0.8))
        self.s.recentes.clear()
        self.assertEqual(self.s._voz_dbfs(), -120.0)  # sem áudio: silêncio, nunca erro
        self.s.recentes.append(b"")
        self.assertEqual(self.s._voz_dbfs(), -120.0)

    async def test_recentes_keeps_the_wait(self):
        # final review 16/11: sized for the config's maximum pre-roll (2 s) plus the claim wait (0.3 s), with margin
        import math, configuracao, arbitragem
        minimo = int(configuracao.PRE_GRAVACAO_MAX_S / 0.08) + 4 + math.ceil(arbitragem.ESPERA_DISPUTA_S / 0.08)
        self.assertGreaterEqual(minimo, 33)
        self.assertGreaterEqual(self.s.recentes.maxlen, minimo)
        self.assertEqual(configuracao._REGRAS["pre_gravacao_s"][2], configuracao.PRE_GRAVACAO_MAX_S)

    async def test_preroll_at_the_config_maximum_covers_the_wait(self):
        self.s.cfg = dict(self.s.cfg, pre_gravacao_s=2)
        self.s.recentes.extend(bytes([i]) * 2 for i in range(60))
        self.s._espera_disputa_s = 0.3
        self.s._agir("conectar", {"wake_word": "friday"})
        enviados = [b[0] for b in F.conexoes[-1].enviados]
        n = int(2 / 0.08) + 4 + 4
        self.assertEqual(len(enviados), n)  # nothing of the 2 s pre-roll is lost to the wait
        self.assertEqual(enviados, list(range(60 - n, 60)))


class TestPararFala(Base):
    async def test_parar_fala_cuts_playback_and_the_rest_of_her_speech(self):
        self.s.maestro.evento("palavra", 0.0)
        self.s._agir("conectar", None)
        self.s._agir("parar_fala", None)
        self.assertEqual((F.parou_ja, F.cortes), (1, 1))

    async def test_pi_delivery_connects_with_the_mic_closed(self):  # 19/11
        self.s.maestro.mudo = False
        _, acoes = self.s.maestro.campainha("e1abcdef", 0.0)
        for tipo, arg in acoes:
            if tipo in ("conectar", "microfone"):
                self.s._agir(tipo, arg)
        self.assertFalse(self.s.microfone_aberto)

    async def test_room_speech_during_the_gate_is_not_told_to_the_connection(self):  # 19/11, revisão M2
        self.s.maestro.mudo = False
        _, acoes = self.s.maestro.campainha("e1abcdef", 0.0)
        for tipo, arg in acoes:
            if tipo in ("conectar", "microfone"):
                self.s._agir(tipo, arg)
        self.s.evento("fala_comecou", None)
        self.assertEqual(F.falas_pessoa, 0)

    async def test_person_speaking_is_told_to_the_connection(self):
        self.s.maestro.evento("palavra", 0.0)
        self.s._agir("conectar", None)
        self.s.evento("fala_comecou", None)
        self.assertEqual(F.falas_pessoa, 1)


class LinkFalso:
    """XvfLink falso (spec 2026-11-17): registra escritas."""
    def __init__(self):
        self.escritas = []

    def write(self, nome, valores):
        self.escritas.append((nome, list(valores)))
        return True

    def read(self, nome):
        return None


class TestXvf3800(Base):
    async def asyncSetUp(self):
        self.link = LinkFalso()
        self.p_link = mock.patch.object(fs.xvf_link, "XvfLink", return_value=self.link)
        self.p_link.start()
        await super().asyncSetUp()

    async def asyncTearDown(self):
        await super().asyncTearDown()
        self.p_link.stop()

    def _novo_satelite_xvf(self):
        self.p_placa.stop()
        self.p_placa = mock.patch.object(fs.placa, "ler_placa", return_value="xvf3800")
        self.p_placa.start()
        self.link.escritas.clear()
        return fs.Satelite(asyncio.get_running_loop())

    async def test_startup_reapplies_the_board_settings_unlocking_first(self):
        s = self._novo_satelite_xvf()
        self.assertEqual(self.link.escritas[0], ("AEC_FIXEDBEAMSONOFF", [0]))
        self.assertIn(("AUDIO_MGR_OP_R", [7, 3]), self.link.escritas)
        self.assertEqual(s.placa, "xvf3800")
        self.assertIsInstance(s.anel, fs.ring.Xvf3800Ring)  # a placa do audio-select decide, não a busca USB do anel

    async def test_button_mute_turns_the_ring_red_on_the_xvf(self):
        s = self._novo_satelite_xvf()
        s.maestro.mudo = True  # o detector viu os zeros do botão
        self.assertTrue(s._mudo_visivel())

    async def test_undo_mute_writes_the_pin_on_the_xvf(self):
        s = self._novo_satelite_xvf()
        self.link.escritas.clear()
        s._agir("desfazer_mudo", None)
        self.assertIn(("GPO_WRITE_VALUE", [30, 0]), self.link.escritas)

    async def test_jabra_ring_ignores_the_button_mute_as_today(self):
        self.s.maestro.mudo = True
        self.assertFalse(self.s._mudo_visivel())


class TestXvf3800Atraso(TestXvf3800):
    async def test_delay_change_from_ha_is_written_live(self):
        s = self._novo_satelite_xvf()
        self.link.escritas.clear()
        s.ao_mudar_config(dict(s.cfg, xvf_sys_delay=3))
        self.assertEqual(self.link.escritas, [("AUDIO_MGR_SYS_DELAY", [3])])


class TestXvf3800Trava(TestXvf3800):
    """DOA v0 (17/11): trava na palavra, solta no fim do primeiro pedido e ao fechar a conversa."""
    def _xvf_com_direcao(self, graus=45):
        s = self._novo_satelite_xvf()
        agora = fs.time.monotonic()
        for dt in (0.6, 0.4, 0.2):
            s.doa_dir.alimentar(agora - dt, fs.math.radians(graus))
        self.link.escritas.clear()
        return s

    async def test_wake_word_locks_and_first_request_end_releases(self):
        s = self._xvf_com_direcao()
        s._travar_na_palavra()
        self.assertIn(("AEC_FIXEDBEAMSONOFF", [1]), self.link.escritas)
        self.assertAlmostEqual(s.anel._dir_fixa, 45, delta=1)
        s._doa_evento("fala_parou")  # fim da própria palavra: segue travada
        self.assertTrue(s.doa_trava.travado)
        s._doa_evento("fala_comecou"); s._doa_evento("fala_parou")
        self.assertFalse(s.doa_trava.travado)
        self.assertIn(("AUDIO_MGR_OP_R", [7, 3]), self.link.escritas)

    async def test_beam_lock_switch_off_never_locks(self):
        s = self._xvf_com_direcao()
        s.cfg["beam_lock"] = False
        s._travar_na_palavra()
        self.assertNotIn(("AEC_FIXEDBEAMSONOFF", [1]), self.link.escritas)

    async def test_conversation_close_releases_and_clears_the_ring(self):
        s = self._xvf_com_direcao()
        s._travar_na_palavra()
        s._doa_evento("parada")
        self.assertFalse(s.doa_trava.travado)
        self.assertIsNone(s.anel._dir_fixa)

    async def test_jabra_has_no_beam_lock(self):
        self.assertIsNone(self.s.doa_regra)
        self.s._travar_na_palavra()  # não faz nada no Jabra


class TestXvf3800MudoRevisao(TestXvf3800):
    async def test_button_mute_is_saved_so_a_restart_keeps_it(self):
        # review I1: apply_startup writes the pin from cfg["mudo"]; a button mute must be recorded there
        s = self._novo_satelite_xvf()
        s.maestro.mudo = False
        s.evento("mudo", True)
        self.assertTrue(s.cfg["mudo"])
        self.assertTrue(fs.cfgmod.carregar(fs.CFG)["mudo"])

    async def test_undo_mute_never_lifts_a_mute_the_user_made(self):
        # review I2: a mute from HA or the button (cfg) stays when the network comes back
        s = self._novo_satelite_xvf()
        s.cfg["mudo"] = True
        self.link.escritas.clear()
        s._agir("desfazer_mudo", None)
        self.assertNotIn(("GPO_WRITE_VALUE", [30, 0]), self.link.escritas)
