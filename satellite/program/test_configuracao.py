"""cd /homeassistant/tools/satelite/programa && python3 -m unittest test_configuracao -v"""
import json, os, re, stat, tempfile, unittest
import configuracao as c


class TestPadroesGravacao(unittest.TestCase):
    def test_gravacoes_ligadas_de_fabrica(self):  # 18/11: ativações e ambiente (08:00-22:00) por padrão
        self.assertIs(c.PADROES["gravar_ativacoes"], True)
        self.assertEqual(c.PADROES["gravar_ambiente_janela"], "08:00-22:00")


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

    def test_keep_listening_while_speaking(self):  # 10/11: nasce ligado; só booleano
        self.assertIs(c.PADROES["keep_listening_while_speaking"], True)
        self.assertIs(c.validar_mudancas(dict(c.PADROES), {"keep_listening_while_speaking": False})[
            "keep_listening_while_speaking"], False)
        with self.assertRaises(ValueError):
            c.validar_mudancas(dict(c.PADROES), {"keep_listening_while_speaking": 1})

    def test_speech_max_s_zero_turns_it_off(self):  # 10/11: 0 = sem teto; 1..4 continua recusado
        self.assertEqual(c.validar_mudancas(dict(c.PADROES), {"speech_max_s": 0})["speech_max_s"], 0)
        for v in (0.5, 4, -1, False):
            with self.assertRaises(ValueError):
                c.validar_mudancas(dict(c.PADROES), {"speech_max_s": v})


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

class TestTwoBeam(unittest.TestCase):
    def test_two_beam_defaults(self):
        p = c.PADROES
        self.assertIs(p["two_beam_listening"], True)
        self.assertIsNone(p["tv_azimuth_deg"])
        self.assertEqual((p["tv_elevation_deg"], p["caller_margin_db"], p["tv_margin_db"],
                          p["caller_silence_s"], p["turn_max_s"]), (0, 3.0, 6.0, 1.5, 60))

    def test_tv_azimuth_none_allowed_and_range_checked(self):
        base = dict(c.PADROES)
        self.assertIsNone(c.validar_mudancas(base, {"tv_azimuth_deg": None})["tv_azimuth_deg"])
        self.assertEqual(c.validar_mudancas(base, {"tv_azimuth_deg": 359.9})["tv_azimuth_deg"], 359.9)
        for ruim in (360, -1, "90", True):
            with self.assertRaises(ValueError, msg=ruim):
                c.validar_mudancas(base, {"tv_azimuth_deg": ruim})
        with self.assertRaises(ValueError):
            c.validar_mudancas(base, {"tv_elevation_deg": 81})
        with self.assertRaises(ValueError):
            c.validar_mudancas(base, {"turn_max_s": 9})

    def test_pi_turns_in_connect_body_only_when_true(self):
        self.assertIs(c.dados_conexao("sat-1", {}, {"pi_turns": True})["pi_turns"], True)
        for v in (False, None, 1, "yes"):
            self.assertNotIn("pi_turns", c.dados_conexao("sat-1", {}, {"pi_turns": v}))
        self.assertNotIn("pi_turns", c.dados_conexao("sat-1", {}))

    def test_two_beam_needs_xvf_and_only_word_interrupts(self):
        cfg = dict(c.PADROES)
        self.assertTrue(c.dois_feixes_permitido(cfg, "xvf3800"))
        self.assertFalse(c.dois_feixes_permitido(cfg, "outra"))
        self.assertFalse(c.dois_feixes_permitido({**cfg, "two_beam_listening": False}, "xvf3800"))
        self.assertFalse(c.dois_feixes_permitido({**cfg, "only_wake_word_interrupts": False}, "xvf3800"))


class TestVolumeFino(unittest.TestCase):
    def _carregar(self, **gravado):
        import json, shutil, tempfile, os
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        p = os.path.join(d, "config.json")
        with open(p, "w") as f:
            json.dump(dict(gravado, senha_porta_local="x" * 32), f)
        cfg = c.carregar(p, os.path.join(d, "house.json"))
        return cfg.get("volume_pct"), cfg.get("volume")

    def test_new_satellite_has_no_volume(self):
        self.assertEqual(self._carregar(), (None, None))

    def test_old_config_keeps_the_loudness(self):
        self.assertEqual(self._carregar(volume=9), (82, 9))

    def test_consistent_pair_keeps_the_percent(self):
        self.assertEqual(self._carregar(volume_pct=70, volume=8), (70, 8))

    def test_rollback_change_wins(self):
        self.assertEqual(self._carregar(volume_pct=70, volume=3), (27, 3))

    def test_zero_percent_never_stores_legacy_zero(self):
        # a versão anterior trata a posição 0 do XVF como quase mudo (rollback)
        self.assertEqual(self._carregar(volume_pct=0, volume=1), (0, 1))
        self.assertEqual(self._carregar(volume=0), (0, 1))
        atual = dict(c.PADROES, senha_porta_local="x" * 32, volume=8, volume_pct=70)
        for mudanca in ({"volume_pct": 0}, {"volume": 0}):
            n = c.validar_mudancas(atual, mudanca)
            self.assertEqual((n["volume_pct"], n["volume"]), (0, 1))
        self.assertEqual(self._carregar(**{k: n[k] for k in ("volume_pct", "volume")}), (0, 1))

    def test_leftover_webhook_key_still_loads(self):
        self.assertEqual(self._carregar(volume_webhook_url="http://x/api/webhook/y", volume=3), (27, 3))
        self.assertNotIn("volume_webhook_url", c.PADROES)

    def test_changes_keep_the_pair_consistent(self):
        atual = dict(c.PADROES, senha_porta_local="x" * 32, volume=8, volume_pct=70)
        n = c.validar_mudancas(atual, {"volume_pct": 75})
        self.assertEqual((n["volume_pct"], n["volume"]), (75, 9))
        n = c.validar_mudancas(atual, {"volume": 3})
        self.assertEqual((n["volume_pct"], n["volume"]), (27, 3))
        n = c.validar_mudancas(atual, {"nao_perturbe": True})
        self.assertEqual((n["volume_pct"], n["volume"]), (70, 8))
        for ruim in (101, -1, 5.5, True):
            with self.assertRaises(ValueError):
                c.validar_mudancas(atual, {"volume_pct": ruim})


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
        # 18/11 (dono): satélite novo nasce com as duas palavras ativas; a Sexta-feira sem verificador (só existe
        # depois do cadastro de voz) e com a paciência de 2 quadros do MK1
        self.assertEqual(c.PADROES["palavras_extras"], [{"nome": "sexta_feira", "arquivo": "/home/jose/modelos/sexta_feira.onnx",
                                                         "limiar": 0.5, "modo": "ativa", "paciencia": 2}])
        self.assertEqual(c.validar_palavras_extras(c.PADROES["palavras_extras"]), c.PADROES["palavras_extras"])

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
        self.assertIs(c.PADROES["only_wake_word_interrupts"], True)
        self.assertIs(c.validar_mudancas(dict(c.PADROES), {"only_wake_word_interrupts": False})["only_wake_word_interrupts"], False)
        with self.assertRaises(ValueError):
            c.validar_mudancas(dict(c.PADROES), {"only_wake_word_interrupts": 1})
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


class TestEnderecoObrigatorio(unittest.TestCase):
    def test_missing_server_address_names_key_and_file(self):
        with self.assertRaises(c.ConfiguracaoIncompleta) as cm:
            c.exigir_endereco({"endereco_friday": None})
        self.assertIn("endereco_friday", str(cm.exception))
        self.assertIn("/etc/friday-satelite/house.json", str(cm.exception))

    def test_present_address_passes(self):
        c.exigir_endereco({"endereco_friday": "https://192.0.2.20:7443"})


class TestVersaoPendente(unittest.TestCase):
    """install.sh grava FRIDAY_VERSION.pending; o programa só o promove ao arrancar, com o código novo carregado."""

    def test_pending_promoted_at_start(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "FRIDAY_VERSION.pending"), "w") as f:
                f.write("v0.2.2 abc\n")
            with open(os.path.join(d, "FRIDAY_VERSION"), "w") as f:
                f.write("v0.2.1 def\n")
            c.promover_versao(d)
            self.assertFalse(os.path.exists(os.path.join(d, "FRIDAY_VERSION.pending")))
            with open(os.path.join(d, "FRIDAY_VERSION")) as f:
                self.assertEqual(f.read(), "v0.2.2 abc\n")

    def test_no_pending_keeps_version(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "FRIDAY_VERSION"), "w") as f:
                f.write("v0.2.1 def\n")
            c.promover_versao(d)
            with open(os.path.join(d, "FRIDAY_VERSION")) as f:
                self.assertEqual(f.read(), "v0.2.1 def\n")


class TestConversaCanal0(unittest.TestCase):
    """18/11 (dono, opção b): no XVF a conversa vai pelo canal 0; desligável pela config para comparar."""
    def test_padrao_ligado_e_so_booleano(self):
        self.assertIs(c.PADROES["xvf_conversa_canal0"], True)
        self.assertIs(c.validar_mudancas(c.PADROES, {"xvf_conversa_canal0": False})["xvf_conversa_canal0"], False)
        with self.assertRaises(ValueError):
            c.validar_mudancas(c.PADROES, {"xvf_conversa_canal0": "sim"})



class TestMusicRelaxedWake(unittest.TestCase):
    """27/11: music-tolerant wake word (music_wake.py)."""
    def test_defaults(self):
        self.assertIsNone(c.PADROES["music_relaxed_wake"])
        self.assertEqual(c.PADROES["music_voice_threshold"], 0.35)
        self.assertEqual(c.PADROES["music_hold_s"], 5.0)

    def test_relaxed_accepts_bool_and_null(self):
        for v in (True, False, None):
            self.assertIs(c.validar_mudancas(dict(c.PADROES), {"music_relaxed_wake": v})["music_relaxed_wake"], v)
        with self.assertRaises(ValueError):
            c.validar_mudancas(dict(c.PADROES), {"music_relaxed_wake": 1})

    def test_bounds(self):
        with self.assertRaises(ValueError):
            c.validar_mudancas(dict(c.PADROES), {"music_voice_threshold": 1.0})
        with self.assertRaises(ValueError):
            c.validar_mudancas(dict(c.PADROES), {"music_hold_s": 31})
        n = c.validar_mudancas(dict(c.PADROES), {"music_voice_threshold": 0, "music_hold_s": 0})
        self.assertEqual((n["music_voice_threshold"], n["music_hold_s"]), (0, 0))
