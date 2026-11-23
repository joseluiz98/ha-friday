"""cd /homeassistant/tools/satelite/programa && python3 -m unittest test_cadastro -v"""
import datetime, json, os, tempfile, unittest, wave
import cadastro as ca

DUR = {"intro": 5.0, "bipe": 0.07, "mudar_lugar": 4.0, "fala_livre": 4.0, "fim": 3.0}


class TestNome(unittest.TestCase):
    def test_nome_vira_id_sem_acento(self):
        self.assertEqual(ca.nome_valido("  Bia "), ("Bia", "bia"))
        self.assertEqual(ca.nome_valido("Inês"), ("Inês", "ines"))
        self.assertEqual(ca.nome_valido("Ana-Clara  Souza"), ("Ana-Clara Souza", "ana_clara_souza"))

    def test_nome_com_e_sem_acento_sao_a_mesma_pessoa(self):
        self.assertEqual(ca.nome_valido("Ines")[1], ca.nome_valido("Inês")[1])

    def test_nome_invalido(self):
        for ruim in (None, "", "   ", "K" * 21, "Bia1", "rm -rf /", "../x", "_negativos"):
            with self.assertRaises(ValueError, msg=ruim):
                ca.nome_valido(ruim)


class TestRoteiro(unittest.TestCase):
    def test_ordem_e_contagem(self):
        r = ca.Roteiro(DUR, 100.0)
        a = r.tique(1e9)
        sons = [v for t, v in a if t == "som"]
        self.assertEqual(sons[0], "intro")
        self.assertEqual(sons.count("bipe"), 10)
        self.assertEqual(sons.index("mudar_lugar"), 6)   # intro + 5 bipes
        self.assertEqual(sons[-2:], ["fala_livre", "fim"])
        janelas = [v for t, v in a if t == "janela"]
        self.assertEqual(janelas, [f"tentativa_{k:02d}" for k in range(1, 11)] + ["fala_livre"])
        self.assertEqual(sum(1 for t, v in a if t == "fechar_janela"), 11)
        self.assertEqual(a[-1], ("terminar", None))
        self.assertTrue(r.terminou)

    def test_janela_abre_so_depois_do_som(self):
        r = ca.Roteiro(DUR, 0.0)
        t_bipe = next(t for t, a, v in r.passos if v == "bipe")
        t_jan = next(t for t, a, v in r.passos if a == "janela")
        self.assertGreaterEqual(t_jan, t_bipe + DUR["bipe"])
        t_livre = next(t for t, a, v in r.passos if v == "fala_livre" and a == "som")
        t_jan_livre = next(t for t, a, v in r.passos if v == "fala_livre" and a == "janela")
        self.assertGreaterEqual(t_jan_livre, t_livre + DUR["fala_livre"])

    def test_tempos_em_ordem(self):
        r = ca.Roteiro(DUR, 0.0)
        tempos = [t for t, a, v in r.passos]
        self.assertEqual(tempos, sorted(tempos))

    def test_tique_devolve_so_o_vencido(self):
        r = ca.Roteiro(DUR, 0.0)
        self.assertEqual(r.tique(0.0), [("som", "intro")])
        self.assertEqual(r.tique(1.0), [])

    def test_duracao_prevista_bate_com_a_constante(self):
        self.assertLessEqual(abs(ca.Roteiro(ca.DURACOES_PADRAO, 0.0).duracao_s - ca.GRAVACAO_PREVISTA_S), 20)


class TestGravador(unittest.TestCase):
    def test_grava_so_com_janela_aberta(self):
        with tempfile.TemporaryDirectory() as d:
            g = ca.Gravador(os.path.join(d, "novo"))
            g.alimentar(b"\x01\x00" * 1280)
            g.abrir("tentativa_01"); g.alimentar(b"\x02\x00" * 1280); g.alimentar(b"\x03\x00" * 1280)
            caminho = g.fechar()
            g.alimentar(b"\x04\x00" * 1280)
            with wave.open(caminho) as w:
                self.assertEqual((w.getframerate(), w.getnchannels(), w.getnframes()), (16000, 1, 2560))
            self.assertIsNone(g.fechar())
            self.assertEqual(oct(os.stat(os.path.join(d, "novo")).st_mode & 0o777), "0o700")


class TestGravacaoContinua(unittest.TestCase):
    """16/10: o modelo base precisa do áudio contínuo (recortes com começo a frio perdem nota: 25 × 33 de 40 nas
    tentativas de 08/10). O gravador guarda a sessão inteira desde a 1ª janela e as marcas de cada janela."""

    def test_sessao_e_marcas(self):
        with tempfile.TemporaryDirectory() as d:
            g = ca.Gravador(os.path.join(d, "novo"))
            g.alimentar(b"\x01\x00" * 1280)                       # antes da 1ª janela: fora
            g.abrir("tentativa_01"); g.alimentar(b"\x02\x00" * 1280); g.alimentar(b"\x02\x00" * 1280); g.fechar()
            g.alimentar(b"\x03\x00" * 1280)                       # entre janelas: entra na sessão
            g.abrir("fala_livre"); g.alimentar(b"\x04\x00" * 1280); g.fechar()
            caminho = g.finalizar()
            with wave.open(caminho) as w:
                self.assertEqual((w.getframerate(), w.getnframes()), (16000, 4 * 1280))
            marcas = json.load(open(os.path.join(d, "novo", "marcas.json")))
            self.assertEqual(marcas, [{"nome": "tentativa_01", "ini": 0, "fim": 2}, {"nome": "fala_livre", "ini": 3, "fim": 4}])

    def test_finalizar_sem_janela_nao_grava(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(ca.Gravador(os.path.join(d, "novo")).finalizar())


class TestVozes(unittest.TestCase):
    def test_listar_aprovar_descartar_e_limpar(self):
        with tempfile.TemporaryDirectory() as d:
            v, agora = ca.Vozes(d), datetime.datetime(2026, 10, 15, 18, 0)
            self.assertEqual(v.listar(), [])
            p = v.pasta_nova("bia", agora); os.makedirs(p)
            open(os.path.join(p, "tentativa_01.wav"), "w").close()
            v.aprovar("bia", "Bia", p, {"aceitas": 9}, agora)
            self.assertEqual(v.listar(), [{"nome": "Bia", "id": "bia", "desde": "2026-10-15 18:00"}])
            self.assertTrue(os.path.isfile(os.path.join(d, "bia", "atual", "tentativa_01.wav")))
            self.assertEqual(v.aceitas_de("bia"), 9)
            p2 = v.pasta_nova("bia", agora); os.makedirs(p2)
            v.descartar(p2)
            self.assertFalse(os.path.exists(p2))
            p3 = v.pasta_nova("jose", agora); os.makedirs(p3)
            self.assertEqual(v.limpar_novos(), 1)
            self.assertFalse(os.path.exists(p3))
            self.assertIsNone(v.aceitas_de("ninguem"))

    def test_aprovar_de_novo_substitui_o_anterior(self):
        with tempfile.TemporaryDirectory() as d:
            v, agora = ca.Vozes(d), datetime.datetime(2026, 10, 15, 18, 0)
            for n in ("a.wav", "b.wav"):
                p = v.pasta_nova("bia", agora); os.makedirs(p)
                open(os.path.join(p, n), "w").close()
                v.aprovar("bia", "Bia", p, {"aceitas": 8}, agora)
            self.assertEqual(os.listdir(os.path.join(d, "bia", "atual")), ["b.wav"])

    def test_reprovado_fica_guardado_um_so(self):
        # 16/10: o cadastro reprovado era apagado e não deu para diagnosticar; fica o último, fora da limpeza
        with tempfile.TemporaryDirectory() as d:
            v, agora = ca.Vozes(d), datetime.datetime(2026, 10, 16, 12, 0)
            for n in ("a.wav", "b.wav"):
                p = v.pasta_nova("jose", agora); os.makedirs(p)
                open(os.path.join(p, n), "w").close()
                v.guardar_reprovado("jose", p)
            self.assertEqual(os.listdir(os.path.join(d, "jose", "reprovado")), ["b.wav"])
            self.assertEqual(v.limpar_novos(), 0)
            self.assertEqual(v.listar(), [])

    def test_pastas_com_sublinhado_nao_sao_pessoas(self):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "_negativos"))
            self.assertEqual(ca.Vozes(d).listar(), [])


DUR2 = dict(DUR, intro_sexta_feira=6.0)
PEDACO = b"\x01\x00" * 1280


def sessao(pasta, marcas, pedacos):
    """Pasta de sessão com `pedacos` pedaços de 80 ms e as marcas dadas."""
    os.makedirs(pasta, exist_ok=True)
    with wave.open(os.path.join(pasta, "sessao.wav"), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000); w.writeframes(PEDACO * pedacos)
    with open(os.path.join(pasta, "marcas.json"), "w") as f:
        json.dump(marcas, f)


class TestRoteiroDuasPalavras(unittest.TestCase):
    def sons_e_janelas(self, r):
        return [(a, v) for _, a, v in r.passos if a in ("som", "janela") and v != "bipe"]

    def test_uma_palavra_e_como_sempre_foi(self):
        self.assertEqual(ca.Roteiro(DUR, 0.0).passos, ca.Roteiro(DUR, 0.0, ("friday",)).passos)

    def test_duas_palavras_cada_uma_com_sua_introducao_e_a_troca_de_lugar(self):
        r = ca.Roteiro(DUR2, 0.0, ("friday", "sexta_feira"))
        seq = self.sons_e_janelas(r)
        sons = [v for a, v in seq if a == "som"]
        self.assertEqual(sons, ["intro", "mudar_lugar", "intro_sexta_feira", "mudar_lugar", "fala_livre", "fim"])
        janelas = [v for a, v in seq if a == "janela"]
        self.assertEqual(janelas[:2] + janelas[9:11], ["tentativa_01", "tentativa_02", "tentativa_10", "sexta_feira__tentativa_01"])
        self.assertEqual(len([j for j in janelas if j.startswith("sexta_feira__")]), 10)
        self.assertEqual(janelas[-1], "fala_livre")

    def test_a_ordem_no_tempo_nunca_volta(self):
        ts = [t for t, *_ in ca.Roteiro(DUR2, 0.0, ("friday", "sexta_feira")).passos]
        self.assertEqual(ts, sorted(ts))

    def test_duracao_prevista_cresce_com_as_palavras(self):
        um = ca.Roteiro(ca.DURACOES_PADRAO, 0.0).duracao_s
        dois = ca.Roteiro(dict(ca.DURACOES_PADRAO, intro_sexta_feira=8.0), 0.0, ("friday", "sexta_feira")).duracao_s
        self.assertLessEqual(abs(dois - um - ca.POR_PALAVRA_EXTRA_S), 15)
        self.assertEqual(ca.gravacao_prevista_s(2), ca.GRAVACAO_PREVISTA_S + ca.POR_PALAVRA_EXTRA_S)
        self.assertEqual(ca.gravacao_prevista_s(1), ca.GRAVACAO_PREVISTA_S)


class TestMarcasPorPalavra(unittest.TestCase):
    def test_a_marca_da_palavra_extra_leva_o_nome_dela_e_a_principal_nao(self):
        with tempfile.TemporaryDirectory() as d:
            g = ca.Gravador(os.path.join(d, "novo"))
            g.abrir("tentativa_01"); g.alimentar(PEDACO); g.fechar()
            g.abrir("sexta_feira__tentativa_01"); g.alimentar(PEDACO); g.fechar()
            g.abrir("fala_livre"); g.alimentar(PEDACO); g.fechar()
            g.finalizar()
            marcas = json.load(open(os.path.join(d, "novo", "marcas.json")))
            self.assertEqual(marcas, [{"nome": "tentativa_01", "ini": 0, "fim": 1},
                                      {"nome": "sexta_feira__tentativa_01", "ini": 1, "fim": 2, "palavra": "sexta_feira"},
                                      {"nome": "fala_livre", "ini": 2, "fim": 3}])


class TestVozesPorPalavra(unittest.TestCase):
    agora = datetime.datetime(2026, 11, 23, 18, 0)

    def test_cadastro_antigo_conta_como_so_a_principal(self):
        with tempfile.TemporaryDirectory() as d:
            v = ca.Vozes(d)
            p = v.pasta_nova("bia", self.agora); sessao(p, [{"nome": "tentativa_01", "ini": 0, "fim": 2}], 4)
            v.aprovar("bia", "Bia", p, {"aceitas": 9}, self.agora)
            self.assertEqual(v.palavras_de("bia"), ["friday"])
            self.assertEqual(v.aceitas_de("bia"), 9)
            self.assertIsNone(v.aceitas_de("bia", "sexta_feira"))
            self.assertEqual(v.palavras_de("ninguem"), [])

    def test_palavra_nova_junta_a_sessao_e_mantem_a_antiga(self):
        with tempfile.TemporaryDirectory() as d:
            v = ca.Vozes(d)
            p1 = v.pasta_nova("bia", self.agora)
            sessao(p1, [{"nome": "tentativa_01", "ini": 0, "fim": 2}, {"nome": "fala_livre", "ini": 3, "fim": 4}], 4)
            v.aprovar("bia", "Bia", p1, {"aceitas": 9}, self.agora)
            p2 = v.pasta_nova("bia", self.agora + datetime.timedelta(days=1))
            sessao(p2, [{"nome": "sexta_feira__tentativa_01", "ini": 0, "fim": 2, "palavra": "sexta_feira"},
                        {"nome": "fala_livre", "ini": 3, "fim": 5}], 5)
            v.aprovar("bia", "Bia", p2, {}, self.agora, {"sexta_feira": {"aceitas": 8}})
            self.assertFalse(os.path.exists(p2))
            self.assertEqual(v.palavras_de("bia"), ["friday", "sexta_feira"])
            self.assertEqual(v.aceitas_de("bia", "friday"), 9)
            self.assertEqual(v.aceitas_de("bia", "sexta_feira"), 8)
            atual = os.path.join(d, "bia", "atual")
            with wave.open(os.path.join(atual, "sessao.wav")) as w:
                self.assertEqual(w.getnframes(), 9 * 1280)
            marcas = json.load(open(os.path.join(atual, "marcas.json")))
            self.assertEqual(marcas, [{"nome": "tentativa_01", "ini": 0, "fim": 2}, {"nome": "fala_livre", "ini": 3, "fim": 4},
                                      {"nome": "sexta_feira__tentativa_01", "ini": 4, "fim": 6, "palavra": "sexta_feira"},
                                      {"nome": "fala_livre", "ini": 7, "fim": 9}])

    def test_regravar_a_mesma_palavra_troca_so_as_marcas_dela(self):
        with tempfile.TemporaryDirectory() as d:
            v = ca.Vozes(d)
            p1 = v.pasta_nova("bia", self.agora)
            sessao(p1, [{"nome": "tentativa_01", "ini": 0, "fim": 2},
                        {"nome": "sexta_feira__tentativa_01", "ini": 2, "fim": 4, "palavra": "sexta_feira"}], 4)
            v.aprovar("bia", "Bia", p1, {}, self.agora, {"friday": {"aceitas": 9}, "sexta_feira": {"aceitas": 8}})
            p2 = v.pasta_nova("bia", self.agora + datetime.timedelta(days=1))
            sessao(p2, [{"nome": "sexta_feira__tentativa_01", "ini": 0, "fim": 2, "palavra": "sexta_feira"}], 2)
            v.aprovar("bia", "Bia", p2, {}, self.agora, {"sexta_feira": {"aceitas": 10}})
            marcas = json.load(open(os.path.join(d, "bia", "atual", "marcas.json")))
            self.assertEqual([m["nome"] for m in marcas], ["tentativa_01", "sexta_feira__tentativa_01"])
            self.assertEqual(marcas[1]["ini"], 4)
            self.assertEqual(v.aceitas_de("bia", "sexta_feira"), 10)
            self.assertEqual(v.aceitas_de("bia", "friday"), 9)

    def test_sessao_que_cobre_todas_as_palavras_substitui_tudo(self):
        with tempfile.TemporaryDirectory() as d:
            v = ca.Vozes(d)
            p1 = v.pasta_nova("bia", self.agora); sessao(p1, [{"nome": "tentativa_01", "ini": 0, "fim": 2}], 4)
            v.aprovar("bia", "Bia", p1, {"aceitas": 9}, self.agora)
            p2 = v.pasta_nova("bia", self.agora + datetime.timedelta(days=1))
            sessao(p2, [{"nome": "tentativa_01", "ini": 0, "fim": 2}], 3)
            v.aprovar("bia", "Bia", p2, {}, self.agora, {"friday": {"aceitas": 10}})
            with wave.open(os.path.join(d, "bia", "atual", "sessao.wav")) as w:
                self.assertEqual(w.getnframes(), 3 * 1280)            # só a nova: nada foi mantido
            self.assertEqual(v.aceitas_de("bia", "friday"), 10)

    def test_copiar_reprovado_deixa_a_sessao_para_aprovar(self):
        with tempfile.TemporaryDirectory() as d:
            v = ca.Vozes(d)
            p = v.pasta_nova("bia", self.agora); sessao(p, [], 2)
            v.copiar_reprovado("bia", p)
            self.assertTrue(os.path.isfile(os.path.join(d, "bia", "reprovado", "sessao.wav")))
            self.assertTrue(os.path.isdir(p))

    def test_legado_com_palavra_nova_e_sem_sessao_continua_so_com_a_nova(self):
        with tempfile.TemporaryDirectory() as d:
            v = ca.Vozes(d)
            p1 = v.pasta_nova("bia", self.agora); os.makedirs(p1); open(os.path.join(p1, "tentativa_01.wav"), "wb").close()
            v.aprovar("bia", "Bia", p1, {"aceitas": 9}, self.agora)
            p2 = v.pasta_nova("bia", self.agora + datetime.timedelta(days=1))
            sessao(p2, [{"nome": "sexta_feira__tentativa_01", "ini": 0, "fim": 2, "palavra": "sexta_feira"}], 2)
            v.aprovar("bia", "Bia", p2, {}, self.agora, {"sexta_feira": {"aceitas": 8}})
            self.assertEqual(v.palavras_de("bia"), ["sexta_feira"])
