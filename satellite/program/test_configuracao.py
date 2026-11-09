"""cd /homeassistant/tools/satelite/programa && python3 -m unittest test_configuracao -v"""
import json, os, re, stat, tempfile, unittest
import configuracao as c


class TestId(unittest.TestCase):
    def test_gera_uma_vez_e_reaproveita(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "id")
            a = c.ler_ou_criar_id(p); b = c.ler_ou_criar_id(p)
            self.assertRegex(a, r"^sat-[0-9a-f]{6}$"); self.assertEqual(a, b)


class TestConfig(unittest.TestCase):
    def test_carregar_cria_com_padroes_e_senha(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "config.json")
            cfg = c.carregar(p)
            self.assertEqual(cfg["espera_inicial_s"], 4); self.assertEqual(cfg["continuacao_s"], 5)
            self.assertGreaterEqual(len(cfg["senha_porta_local"]), 32)
            self.assertEqual(stat.S_IMODE(os.stat(p).st_mode), 0o600)

    def test_config_rejeita_invalidos(self):
        atual = dict(c.PADROES, senha_porta_local="x" * 32)
        for ruim in ({"espera_inicial_s": "quatro"}, {"limiar_palavra": 1.5}, {"limiar_voz_palavra": 1.2}, {"comodo": ""}, {"comodo": "x" * 41}, {"comodo": 3}, {"chave_nova": 1},
                     {"senha_porta_local": "troca"}, {"continuacao_s": -1}, {"nao_perturbe": "sim"}):
            with self.assertRaises(ValueError, msg=str(ruim)):
                c.validar_mudancas(atual, ruim)

    def test_config_rejeita_corpo_que_nao_e_objeto(self):
        # M4 (revisão 08/10): JSON válido mas não-objeto (lista, texto, número, null) dava AttributeError
        # (.items() em algo sem .items()) -> a porta local devolvia 500 em vez de 400.
        atual = dict(c.PADROES, senha_porta_local="x" * 32)
        for ruim in ([1, 2, 3], "comodo", 42, None, True):
            with self.assertRaises(ValueError, msg=str(ruim)):
                c.validar_mudancas(atual, ruim)

    def test_validar_aceita_e_mescla(self):
        atual = dict(c.PADROES, senha_porta_local="x" * 32)
        novo = c.validar_mudancas(atual, {"espera_inicial_s": 5, "limiar_palavra": 0.45, "gravar_ativacoes": True,
                                                   "limiar_voz_palavra": 0.7, "comodo": "Escritório"})
        self.assertEqual(novo["comodo"], "Escritório")
        self.assertTrue(novo["gravar_ativacoes"])
        self.assertEqual(novo["espera_inicial_s"], 5); self.assertEqual(novo["continuacao_s"], 5)

    def test_dados_da_conexao_levam_o_comodo(self):
        self.assertEqual(c.dados_conexao("sat-0a1b2c", {"comodo": "Escritório"}),
                         {"origem": "satelite", "satelite_id": "sat-0a1b2c", "comodo": "Escritório"})
        self.assertEqual(c.dados_conexao("sat-0a1b2c", {"comodo": None}),
                         {"origem": "satelite", "satelite_id": "sat-0a1b2c"})
        self.assertTrue(c.dados_conexao("sat-0a1b2c", {"nao_perturbe": True})["nao_perturbe"])

    def test_gravar_e_atomico_e_600(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "config.json")
            c.gravar(p, {"a": 1})
            self.assertEqual(json.load(open(p)), {"a": 1})
            self.assertEqual(stat.S_IMODE(os.stat(p).st_mode), 0o600)
            self.assertEqual([f for f in os.listdir(d) if f != "config.json"], [])


class TestMusicConfig(unittest.TestCase):
    def test_music_duck_percent_bounds(self):
        self.assertEqual(c.PADROES["music_duck_percent"], 15)
        with self.assertRaises(ValueError):
            c.validar_mudancas(dict(c.PADROES), {"music_duck_percent": 26})
        for v in (0, 25):
            self.assertEqual(c.validar_mudancas(dict(c.PADROES), {"music_duck_percent": v})["music_duck_percent"], v)

    def test_music_restore_s_bounds(self):
        self.assertEqual(c.PADROES["music_restore_s"], 1.0)
        with self.assertRaises(ValueError):
            c.validar_mudancas(dict(c.PADROES), {"music_restore_s": 10.5})
        for v in (0, 10):
            self.assertEqual(c.validar_mudancas(dict(c.PADROES), {"music_restore_s": v})["music_restore_s"], v)

    def test_speech_max_s_bounds(self):
        self.assertEqual(c.PADROES["speech_max_s"], 15)
        with self.assertRaises(ValueError):
            c.validar_mudancas(dict(c.PADROES), {"speech_max_s": 4})
        for v in (5, 120):
            self.assertEqual(c.validar_mudancas(dict(c.PADROES), {"speech_max_s": v})["speech_max_s"], v)


class TestAlarmRamp(unittest.TestCase):
    def test_alarm_ramp_s_bounds(self):
        self.assertEqual(c.PADROES["alarm_ramp_s"], 0)
        with self.assertRaises(ValueError):
            c.validar_mudancas(dict(c.PADROES), {"alarm_ramp_s": 121})
        with self.assertRaises(ValueError):
            c.validar_mudancas(dict(c.PADROES), {"alarm_ramp_s": -1})
        for v in (0, 120):
            self.assertEqual(c.validar_mudancas(dict(c.PADROES), {"alarm_ramp_s": v})["alarm_ramp_s"], v)


class TestAgenteUrl(unittest.TestCase):
    def test_padrao_e_validacao(self):
        self.assertEqual(c.PADROES["agente_url"], "http://127.0.0.1:8701")
        atual = dict(c.PADROES, senha_porta_local="x" * 32)
        self.assertIsNone(c.validar_mudancas(atual, {"agente_url": None})["agente_url"])
        self.assertEqual(c.validar_mudancas(atual, {"agente_url": "http://127.0.0.1:9"})["agente_url"], "http://127.0.0.1:9")
        for ruim in (3, "", True):
            with self.assertRaises(ValueError):
                c.validar_mudancas(atual, {"agente_url": ruim})

class TestEcoPortao(unittest.TestCase):
    def test_eco_portao_db_aceita_null_e_faixa(self):
        self.assertIsNone(c.PADROES["eco_portao_db"])
        self.assertEqual(c.validar_mudancas(dict(c.PADROES), {"eco_portao_db": -35})["eco_portao_db"], -35)
        self.assertIsNone(c.validar_mudancas(dict(c.PADROES), {"eco_portao_db": None})["eco_portao_db"])
        for ruim in (5, -95, "x", True):
            with self.assertRaises(ValueError):
                c.validar_mudancas(dict(c.PADROES), {"eco_portao_db": ruim})



class TestEnderecoDaCasa(unittest.TestCase):
    """09/11 (plano 2026-11-09 Task 7): o endereço da VM não fica no código; vem de /etc/friday-satelite/house.json,
    gravado na imagem a partir da configuração local da VM."""

    def test_house_file_fills_server_address(self):
        with tempfile.TemporaryDirectory() as d:
            casa = os.path.join(d, "house.json")
            with open(casa, "w") as f:
                json.dump({"endereco_friday": "https://192.0.2.20:7443"}, f)
            cfg = c.carregar(os.path.join(d, "config.json"), casa=casa)
            self.assertEqual(cfg["endereco_friday"], "https://192.0.2.20:7443")

    def test_config_wins_over_house_file(self):
        with tempfile.TemporaryDirectory() as d:
            casa, p = os.path.join(d, "house.json"), os.path.join(d, "config.json")
            with open(casa, "w") as f:
                json.dump({"endereco_friday": "https://192.0.2.20:7443"}, f)
            with open(p, "w") as f:
                json.dump({"endereco_friday": "https://192.0.2.99:7443"}, f)
            self.assertEqual(c.carregar(p, casa=casa)["endereco_friday"], "https://192.0.2.99:7443")

    def test_without_house_file_there_is_no_built_in_address(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = c.carregar(os.path.join(d, "config.json"), casa=os.path.join(d, "absent.json"))
            self.assertIsNone(cfg["endereco_friday"])

if __name__ == "__main__":
    unittest.main()


class TestVolumeETimers(unittest.TestCase):
    """Spec 2026-10-12: volume (posição 0–11), volume_alertas (1–10), sons novos e extras da conexão."""

    def test_padroes_novos(self):
        self.assertEqual(c.PADROES["volume_alertas"], 6)
        for chave in ("volume", "alarme", "lembrete"):
            self.assertIn(chave, c.PADROES["sons"])

    def test_sons_gravados_antes_ganham_os_novos(self):
        # o config.json da Pi tem só inicio/fim/erro: o update raso apagaria os sons novos
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "config.json")
            with open(p, "w") as f:
                json.dump({"senha_porta_local": "x" * 32, "sons": {"inicio": "inicio_a.wav", "fim": "fim_b.wav",
                                                                    "erro": "erro_b.wav"}}, f)
            cfg = c.carregar(p)
            self.assertEqual(cfg["sons"]["fim"], "fim_b.wav")
            self.assertEqual(cfg["sons"]["alarme"], c.PADROES["sons"]["alarme"])

    def test_sons_do_nao_perturbe_nos_padroes(self):
        self.assertEqual(c.PADROES["sons"]["nao_perturbe_ligou"], "nao_perturbe_ligou.wav")
        self.assertEqual(c.PADROES["sons"]["nao_perturbe_desligou"], "nao_perturbe_desligou.wav")

    def test_config_antiga_ganha_sons_do_nao_perturbe(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "config.json")
            with open(p, "w") as f:
                json.dump({"senha_porta_local": "x" * 32, "sons": {"inicio": "inicio_a.wav", "fim": "fim_b.wav"}}, f)
            cfg = c.carregar(p)
            self.assertEqual(cfg["sons"]["fim"], "fim_b.wav")
            self.assertEqual(cfg["sons"]["nao_perturbe_ligou"], "nao_perturbe_ligou.wav")
            self.assertEqual(cfg["sons"]["nao_perturbe_desligou"], "nao_perturbe_desligou.wav")

    def test_volume_e_posicao_e_alertas_e_escala_falada(self):
        atual = dict(c.PADROES, senha_porta_local="x" * 32)
        self.assertEqual(c.validar_mudancas(atual, {"volume": 11, "volume_alertas": 10})["volume"], 11)
        for ruim in ({"volume": 12}, {"volume": -1}, {"volume": 5.5}, {"volume_alertas": 0},
                     {"volume_alertas": 11}, {"volume_alertas": True}):
            with self.assertRaises(ValueError):
                c.validar_mudancas(atual, ruim)

    def test_dados_conexao_repassa_presentation(self):
        fatos = {"areas": [{"id": "sala", "name": "Sala"}], "known_voices": ["Ana"], "failed_steps": [],
                 "wake_word_ok": True}
        d = c.dados_conexao("sat-1", {"comodo": None}, {"presentation": fatos})
        self.assertEqual(d["presentation"], fatos)
        self.assertEqual(d["origem"], "satelite")

    def test_dados_conexao_com_extras(self):
        d = c.dados_conexao("sat-0a1b2c", {}, {"entrega": "abc12345"})
        self.assertEqual(d["entrega"], "abc12345")
        self.assertEqual(d["origem"], "satelite")
        self.assertNotIn("entrega", c.dados_conexao("sat-0a1b2c", {}))


class TestPalavrasExtras(unittest.TestCase):
    """spec 2026-10-24 (wakeword-retrain): palavras configuráveis."""
    EXTRA = {"nome": "sexta_feira", "arquivo": "/home/jose/modelos/sexta_feira.onnx", "limiar": 0.5, "modo": "sombra"}

    def test_padroes_palavras(self):
        self.assertEqual(c.PADROES["modo_friday"], "ativa")
        self.assertEqual(c.PADROES["palavras_extras"], [])

    def test_extras_valida_ok(self):
        r = c.validar_palavras_extras([dict(self.EXTRA, limiar=1 - 0.5)])
        self.assertEqual(r, [self.EXTRA])
        self.assertIsInstance(r[0]["limiar"], float)
        r = c.validar_palavras_extras([dict(self.EXTRA, limiar=0.99)])
        self.assertEqual(r[0]["limiar"], 0.99)

    def test_extras_rejeita(self):
        e = self.EXTRA
        faltando = dict(e); faltando.pop("modo")
        casos = [[dict(e, modo="x")], [dict(e, limiar=1.2)], [dict(e, limiar=0.01)], [dict(e, nome="outra")],
                 [dict(e, nome="friday", arquivo="/a/friday.onnx")], [e, dict(e)], [faltando], "nao-lista",
                 [dict(e, limiar=True)], ["x"]]
        for caso in casos:
            with self.assertRaises(ValueError, msg=str(caso)) as cm:
                c.validar_palavras_extras(caso)
            self.assertTrue(str(cm.exception).startswith("palavras_extras:"), str(cm.exception))

    def test_extras_paciencia_opcional(self):
        # 02/11: quadros seguidos acima do limiar para disparar; sem o campo, o detector de sempre (1)
        self.assertNotIn("paciencia", c.validar_palavras_extras([self.EXTRA])[0])
        self.assertEqual(c.validar_palavras_extras([dict(self.EXTRA, paciencia=2)])[0]["paciencia"], 2)
        for ruim in (0, 6, 1.5, True, "2"):
            with self.assertRaises(ValueError, msg=str(ruim)):
                c.validar_palavras_extras([dict(self.EXTRA, paciencia=ruim)])

    def test_extras_verificador_opcional(self):
        # 02/11: verificador de voz próprio da palavra extra (caminho do .pkl)
        self.assertNotIn("verificador", c.validar_palavras_extras([self.EXTRA])[0])
        v = "/home/jose/modelos/sexta_feira_verificador.pkl"
        self.assertEqual(c.validar_palavras_extras([dict(self.EXTRA, verificador=v)])[0]["verificador"], v)
        for ruim in ("", 3, True, "x" * 201):
            with self.assertRaises(ValueError, msg=str(ruim)):
                c.validar_palavras_extras([dict(self.EXTRA, verificador=ruim)])

    def test_validar_mudancas_aceita_palavras(self):
        novo = c.validar_mudancas(dict(c.PADROES), {"modo_friday": "sombra", "palavras_extras": [self.EXTRA]})
        self.assertEqual(novo["modo_friday"], "sombra")
        self.assertEqual(novo["palavras_extras"], [self.EXTRA])

    def test_validar_mudancas_modo_friday_invalido(self):
        for v in ("x", None, 3, True):
            with self.assertRaises(ValueError):
                c.validar_mudancas(dict(c.PADROES), {"modo_friday": v})

    def test_validar_mudancas_extras_invalidas(self):
        with self.assertRaises(ValueError):
            c.validar_mudancas(dict(c.PADROES), {"palavras_extras": [dict(self.EXTRA, modo="x")]})


class TestMudoESonsTurno(unittest.TestCase):
    def test_mudo_e_sons_turno_validos(self):
        self.assertIs(c.PADROES["mudo"], False)       # padrão = comportamento de hoje
        self.assertIs(c.PADROES["sons_turno"], True)
        novo = c.validar_mudancas(dict(c.PADROES), {"mudo": True, "sons_turno": False})
        self.assertIs(novo["mudo"], True); self.assertIs(novo["sons_turno"], False)
        for k in ("mudo", "sons_turno"):
            for v in (1, "sim", None):
                with self.assertRaises(ValueError):
                    c.validar_mudancas(dict(c.PADROES), {k: v})

    def test_sons_turno_pula_so_inicio_e_fim(self):
        cfg = dict(c.PADROES, sons_turno=False)
        self.assertFalse(c.som_permitido(cfg, "inicio")); self.assertFalse(c.som_permitido(cfg, "fim"))
        for n in ("erro", "alarme", "volume", "lembrete", "nao_perturbe_ligou"):
            self.assertTrue(c.som_permitido(cfg, n))
        self.assertTrue(c.som_permitido(dict(c.PADROES), "inicio"))
        self.assertTrue(c.som_permitido({}, "fim"))  # config sem a chave = como sempre


class TestJanelaVoz(unittest.TestCase):
    """03/11/2026: quadros de 80 ms em que a porta de voz procura voz humana (6 = 0,48 s, o de sempre)."""
    def test_padrao_e_limites(self):
        self.assertEqual(c.PADROES["janela_voz_palavra"], 6)
        for v in (1, 10, 25):
            self.assertEqual(c.validar_mudancas(dict(c.PADROES), {"janela_voz_palavra": v})["janela_voz_palavra"], v)
        for ruim in (0, 26, 0.8, True, "10"):
            with self.assertRaises(ValueError, msg=repr(ruim)):
                c.validar_mudancas(dict(c.PADROES), {"janela_voz_palavra": ruim})


class TestCloseMicAwaitingReply(unittest.TestCase):
    def test_padrao_desligado_e_aceita_bool(self):
        import configuracao as c
        self.assertIs(c.PADROES["close_mic_awaiting_reply"], False)
        self.assertIs(c.validar_mudancas(c.PADROES, {"close_mic_awaiting_reply": True})["close_mic_awaiting_reply"], True)
        with self.assertRaises(ValueError):
            c.validar_mudancas(c.PADROES, {"close_mic_awaiting_reply": 1})


class TestContinuacaoEstiloAlexa(unittest.TestCase):
    def test_padroes_e_regras(self):
        import configuracao as c
        self.assertIs(c.PADROES["no_follow_up_while_music"], True)
        self.assertIs(c.PADROES["no_follow_up_after_home_commands"], True)
        self.assertEqual(c.PADROES["end_of_speech_pause_s"], 0.8)
        for v in (0.5, 2.0):
            self.assertEqual(c.validar_mudancas(dict(c.PADROES), {"end_of_speech_pause_s": v})["end_of_speech_pause_s"], v)
        for ruim in (0.4, True, 2.1):
            with self.assertRaises(ValueError):
                c.validar_mudancas(dict(c.PADROES), {"end_of_speech_pause_s": ruim})
        with self.assertRaises(ValueError):
            c.validar_mudancas(dict(c.PADROES), {"no_follow_up_while_music": 1})


class TestDadosConexaoPausa(unittest.TestCase):
    def test_pausa_so_vai_quando_difere_do_padrao(self):
        import configuracao as c
        self.assertNotIn("end_of_speech_pause_s", c.dados_conexao("sat-1", dict(c.PADROES)))
        cfg = dict(c.PADROES, end_of_speech_pause_s=1.2)
        self.assertEqual(c.dados_conexao("sat-1", cfg)["end_of_speech_pause_s"], 1.2)
