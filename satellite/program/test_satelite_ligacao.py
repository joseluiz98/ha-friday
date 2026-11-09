"""Ligação do programa (friday_satelite.py) com hardware e rede simulados — spec 2026-10-12.
cd /homeassistant/tools/satelite/programa && python3 -m unittest test_satelite_ligacao -v"""
import asyncio, json, os, sys, tempfile, time, types, unittest
from unittest import mock

# Módulos que só existem na Pi (ou que mexem em hardware) viram simulados SÓ durante o import do programa; depois
# os originais voltam, senão os outros testes da suíte (test_conexao, test_hardware) pegariam os simulados.
_SIMULADOS = ("numpy", "aiohttp", "openwakeword", "openwakeword.model", "openwakeword.vad", "hardware", "conexao",
              "porta_local", "friday_satelite", "whisper")
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


F = Falsos()


class Mixer:
    def ler(self): return 4
    def escrever(self, k): F.escritas.append(k)


class MusicMixer:
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
    def mostrar(self, nome): pass


class Conexao:
    def __init__(self, endereco, dados, ao_evento, ao_audio):
        self.dados, self.enviados = dados, []
        F.conexoes.append(self)
    def enviar(self, pcm): self.enviados.append(pcm)
    def enviar_silencio(self, segundos=1.5): pass
    def enviar_mensagem(self, tipo, dados):
        F.mensagens.append((tipo, dados)); return True
    def silenciar(self): F.silenciadas += 1
    async def abrir(self): pass
    async def fechar(self): F.fechadas += 1


hw = types.ModuleType("hardware")
hw.AltoFalante, hw.Anel, hw.Mixer, hw.MusicMixer = AltoFalante, Anel, Mixer, MusicMixer
hw.ControleMudo = ControleMudo
hw.Botao = hw.Captura = hw.TeclasVolume = object
sys.modules["hardware"] = hw
cx = types.ModuleType("conexao")
cx.ConexaoFriday = Conexao
sys.modules["conexao"] = cx
wh = types.ModuleType("whisper")  # o real exige numpy; aqui só a assinatura (spec 2026-10-20)
wh.whisper_ratio_db = wh.noise_floor_rms = lambda *a, **k: None
sys.modules["whisper"] = wh
pl = types.ModuleType("porta_local")
sys.modules["porta_local"] = pl

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
        self.p = mock.patch.multiple(fs, CFG=os.path.join(self.d, "config.json"), ID=os.path.join(self.d, "id"),
                                     TIMERS=os.path.join(self.d, "estado", "timers.json"), SONS=self.d)
        self.p.start()
        import sons
        sons.gerar(self.d)
        self.s = fs.Satelite(asyncio.get_running_loop())
        import cadastro
        self.s.vozes = cadastro.Vozes(os.path.join(self.d, "vozes"))  # never the Pi's /home/jose/vozes
        self.s.maestro.mudo = False  # a captura decidiria; aqui o microfone está liberado

    async def asyncTearDown(self):
        self.s._parar_alarme()
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
        self.assertEqual(F.sons, ["erro_a.wav"])

    async def test_padrao_toca_os_sons_de_turno(self):
        self.s._agir("som", "inicio"); self.s._agir("som", "fim")
        self.assertEqual(F.sons, ["inicio_a.wav", "fim_a.wav"])


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
        self.assertIn("alarme_a.wav", F.sons)
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
        self.assertIn("lembrete_a.wav", F.sons)
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
        self.assertEqual(F.sons[-1], "fim_a.wav")  # os sons pedidos DEPOIS do cancelamento tocam normalmente
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
        self.s.anel = types.SimpleNamespace(show=lambda estado, direcao=None: vistos.append(estado))
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
        self.assertIn("unsupported_xvf3800", corpo)


if __name__ == "__main__":
    unittest.main()
