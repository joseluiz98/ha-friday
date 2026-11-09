"""Testes dos timers: cd /homeassistant/tools/satelite/programa && python3 -m unittest test_timers -v"""
import unittest
import timers as tm


def novo():
    gravados = []
    return tm.Timers(gravados.append), gravados


class TestCriar(unittest.TestCase):
    def test_cria_com_termino_em_relogio_de_parede_e_grava(self):
        t, g = novo()
        x = t.criar(600, None, 1000.0)
        self.assertEqual((x["duracao_s"], x["termino"], x["nome"]), (600, 1600.0, None))
        self.assertEqual(len(g), 1)
        self.assertEqual(g[-1][0]["id"], x["id"])

    def test_nome_e_limpo(self):
        t, _ = novo()
        self.assertEqual(t.criar(60, "  Arroz ", 0.0)["nome"], "Arroz")
        self.assertEqual(t.criar(60, "", 0.0)["nome"], None)

    def test_limites(self):
        t, _ = novo()
        for s in (0, -5, 24 * 3600 + 1, 1.5, True, "60", None):
            with self.assertRaises(ValueError):
                t.criar(s, None, 0.0)
        self.assertEqual(t.criar(24 * 3600, None, 0.0)["duracao_s"], 86400)

    def test_acima_de_24h_diz_que_e_lembrete(self):
        t, _ = novo()
        with self.assertRaises(ValueError) as e:
            t.criar(90000, None, 0.0)
        self.assertIn("lembrete", str(e.exception))

    def test_maximo_de_10(self):
        t, _ = novo()
        for _ in range(10):
            t.criar(60, None, 0.0)
        with self.assertRaises(ValueError):
            t.criar(60, None, 0.0)

    def test_nome_grande_recusa(self):
        t, _ = novo()
        with self.assertRaises(ValueError):
            t.criar(60, "x" * 41, 0.0)


class TestConsultarCancelar(unittest.TestCase):
    def setUp(self):
        self.t, self.g = novo()
        self.a = self.t.criar(600, "arroz", 0.0)
        self.b = self.t.criar(60, None, 0.0)

    def test_consultar_ordena_por_termino_e_diz_quanto_falta(self):
        lista = self.t.consultar(30.0)
        self.assertEqual([x["id"] for x in lista], [self.b["id"], self.a["id"]])
        self.assertEqual(lista[0]["falta_s"], 30)

    def test_consultar_por_nome_sem_acento_nem_caixa(self):
        self.assertEqual([x["id"] for x in self.t.consultar(0.0, "ARROZ")], [self.a["id"]])
        self.assertEqual(self.t.consultar(0.0, "feijão"), [])

    def test_cancelar_por_nome(self):
        feitos = self.t.cancelar(nome="Arroz")
        self.assertEqual([x["id"] for x in feitos], [self.a["id"]])
        self.assertEqual(len(self.t.consultar(0.0)), 1)

    def test_cancelar_sem_nome_com_um_so_ativo(self):
        self.t.cancelar(nome="arroz")
        self.assertEqual(len(self.t.cancelar()), 1)
        self.assertEqual(self.t.consultar(0.0), [])

    def test_cancelar_sem_nome_com_varios_e_ambiguo(self):
        with self.assertRaises(ValueError):
            self.t.cancelar()

    def test_cancelar_todos(self):
        self.assertEqual(len(self.t.cancelar(todos=True)), 2)
        self.assertEqual(self.t.consultar(0.0), [])

    def test_cancelar_grava(self):
        n = len(self.g)
        self.t.cancelar(todos=True)
        self.assertEqual(len(self.g), n + 1)


class TestId(unittest.TestCase):
    """14/10: cada timer já nascia com id, mas só o nome identificava; dois sem nome não se distinguiam no cancelar."""

    def setUp(self):
        self.t, self.gravado = novo()
        self.a = self.t.criar(600, None, 0.0)
        self.b = self.t.criar(300, None, 0.0)

    def test_cancelar_por_id_so_esse_timer_sem_nome(self):
        feitos = self.t.cancelar(id=self.a["id"])
        self.assertEqual([x["id"] for x in feitos], [self.a["id"]])
        self.assertEqual([x["id"] for x in self.t.consultar(0.0)], [self.b["id"]])

    def test_id_distingue_dois_com_o_mesmo_nome(self):
        c = self.t.criar(900, "arroz", 0.0)
        d = self.t.criar(120, "arroz", 0.0)
        self.assertEqual([x["id"] for x in self.t.cancelar(id=d["id"])], [d["id"]])
        self.assertIn(c["id"], [x["id"] for x in self.t.consultar(0.0)])

    def test_id_tem_prioridade_sobre_o_nome(self):
        c = self.t.criar(900, "arroz", 0.0)
        self.assertEqual([x["id"] for x in self.t.cancelar(nome="arroz", id=self.a["id"])], [self.a["id"]])
        self.assertIn(c["id"], [x["id"] for x in self.t.consultar(0.0)])

    def test_id_desconhecido_nao_cancela_nada(self):
        self.assertEqual(self.t.cancelar(id="t000000"), [])
        self.assertEqual(len(self.t.consultar(0.0)), 2)

    def test_consultar_por_id(self):
        self.assertEqual([x["id"] for x in self.t.consultar(0.0, id=self.b["id"])], [self.b["id"]])

    def test_id_repetido_por_acaso_e_sorteado_de_novo(self):
        # 3 bytes aleatórios: a chance de repetir é ~1 em 3 milhões, mas repetir faria cancelar por id apagar dois timers
        from unittest import mock
        t, _ = novo()
        with mock.patch.object(tm.secrets, "token_hex", side_effect=["aaaaaa", "aaaaaa", "bbbbbb"]):
            a = t.criar(60, None, 0.0)
            b = t.criar(120, None, 0.0)
        self.assertEqual((a["id"], b["id"]), ("taaaaaa", "tbbbbbb"))

    def test_nome_repetido_e_ambiguo_e_nao_cancela_nada(self):
        # 14/10: cancelar por nome apagava todos os timers com aquele nome (dois "arroz" saíam juntos, em silêncio)
        c = self.t.criar(900, "arroz", 0.0)
        d = self.t.criar(120, "Arroz", 0.0)
        with self.assertRaises(ValueError) as e:
            self.t.cancelar(nome="arroz")
        self.assertIn("nome", str(e.exception))
        self.assertEqual(len(self.t.consultar(0.0)), 4)
        self.assertEqual([x["id"] for x in self.t.cancelar(id=c["id"])], [c["id"]])  # pelo id, um só
        self.assertEqual([x["id"] for x in self.t.cancelar(nome="arroz")], [d["id"]])  # agora o nome é de um só

    def test_todos_continua_cancelando_tudo_mesmo_com_nome_repetido(self):
        self.t.criar(900, "arroz", 0.0)
        self.t.criar(120, "arroz", 0.0)
        self.assertEqual(len(self.t.cancelar(todos=True)), 4)

    def test_ids_nao_se_repetem(self):
        t, _ = novo()
        ids = {t.criar(60, None, 0.0)["id"] for _ in range(tm.MAX_TIMERS)}
        self.assertEqual(len(ids), tm.MAX_TIMERS)


class TestVencidos(unittest.TestCase):
    def test_vencidos_saem_da_lista_em_ordem(self):
        t, g = novo()
        a = t.criar(120, "a", 0.0)
        b = t.criar(60, "b", 0.0)
        t.criar(600, "c", 0.0)
        self.assertEqual(t.vencidos(59.0), [])
        self.assertEqual([x["id"] for x in t.vencidos(200.0)], [b["id"], a["id"]])
        self.assertEqual(len(t.consultar(200.0)), 1)
        self.assertEqual(len(g[-1]), 1)


class TestReinicio(unittest.TestCase):
    def test_carregar_separa_ativos_recentes_e_velhos(self):
        dados = [{"id": "a", "nome": None, "duracao_s": 60, "termino": 1000.0},
                 {"id": "b", "nome": "x", "duracao_s": 60, "termino": 1500.0},
                 {"id": "c", "nome": None, "duracao_s": 60, "termino": 2500.0}]
        t, _ = novo()
        recentes, velhos = t.carregar(dados, 2000.0)
        self.assertEqual([x["id"] for x in recentes], ["b"])  # venceu há 500 s (< 10 min)
        self.assertEqual([x["id"] for x in velhos], ["a"])  # venceu há 1000 s
        self.assertEqual([x["id"] for x in t.consultar(2000.0)], ["c"])

    def test_carregar_ignora_lixo(self):
        t, _ = novo()
        recentes, velhos = t.carregar([{"id": 1}, "x", None, {"termino": "a"}], 0.0)
        self.assertEqual((recentes, velhos, t.consultar(0.0)), ([], [], []))
        t.carregar("não é lista", 0.0)
        self.assertEqual(t.consultar(0.0), [])


class TestMusica(unittest.TestCase):
    def test_timer_keeps_music(self):
        t, g = novo()
        x = t.criar(60, "acordar", 1000.0, music="Legião Urbana")
        self.assertEqual(x["music"], "Legião Urbana")
        t3, _ = novo()
        t3.carregar(g[-1], 1010.0)
        self.assertEqual(t3.consultar(1010.0)[0].get("music"), "Legião Urbana")

    def test_timer_without_music_has_no_key(self):
        t, _ = novo()
        self.assertNotIn("music", t.criar(60, None, 1000.0))

    def test_music_is_validated(self):
        t, _ = novo()
        for ruim in ("", "x" * 81, 3, "a\nb"):
            with self.assertRaises(ValueError, msg=repr(ruim)):
                t.criar(60, None, 1000.0, music=ruim)


if __name__ == "__main__":
    unittest.main()
