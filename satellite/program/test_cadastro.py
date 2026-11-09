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
