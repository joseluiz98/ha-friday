"""cd /homeassistant/tools/satelite/programa && python3 -m unittest test_verificador -v"""
import unittest
import verificador as vf


def clipe(cat, grupo, base, rotulo, nota=0.0, vad=0.9):
    return {"cat": cat, "grupo": grupo, "base": base, "vad": [vad] * len(base), "rotulo": rotulo,
            "quadros": vf.quadros_positivos(base) if rotulo else list(range(len(base))), "nota": nota}


class TestDobras(unittest.TestCase):
    def test_mesmo_grupo_mesma_dobra_e_rodizio(self):
        self.assertEqual(vf.dobras(["b", "a", "c", "a", "d", "e", "f"], k=5),
                         {"a": 0, "b": 1, "c": 2, "d": 3, "e": 4, "f": 0})


class TestQuadros(unittest.TestCase):
    def test_positivos(self):
        self.assertEqual(vf.quadros_positivos([0.1, 0.3, 0.29, 0.8]), [1, 3])

    def test_fala_livre_tira_a_vizinhanca_da_palavra(self):
        base = [0.0] * 40
        base[20] = 0.9
        q = vf.quadros_fala_livre(base)
        self.assertNotIn(20, q); self.assertNotIn(8, q); self.assertNotIn(32, q)
        self.assertIn(7, q); self.assertIn(33, q)


class TestAceita(unittest.TestCase):
    def test_verificador_decide_acima_do_limiar_base(self):
        base, vad = [0.0, 0.35, 0.0], [0.9] * 3
        self.assertTrue(vf.aceita(base, [0.0, 0.9, 0.0], vad, 0.3, 0.5, 0.6))
        self.assertFalse(vf.aceita(base, [0.0, 0.2, 0.0], vad, 0.3, 0.5, 0.6))

    def test_abaixo_do_limiar_base_vale_a_nota_base(self):
        self.assertFalse(vf.aceita([0.0, 0.35, 0.0], [0.9] * 3, [0.9] * 3, 0.4, 0.5, 0.6))

    def test_porta_de_voz(self):
        self.assertFalse(vf.aceita([0.0, 0.9, 0.0], [0.9] * 3, [0.1] * 3, 0.3, 0.5, 0.6))


class TestAvaliar(unittest.TestCase):
    def test_cada_clipe_avaliado_so_pelo_modelo_que_nao_o_viu(self):
        clipes = [clipe("nova", f"k:{i}", [0.0, 0.4, 0.0], 1, nota=0.9) for i in range(10)] + \
                 [clipe("falsas", f"f:{i}", [0.0, 0.6, 0.0], 0, nota=0.1) for i in range(5)]
        treinos = []

        def treinar(lista):
            grupos = {c["grupo"] for c in lista}
            treinos.append(grupos)
            return grupos

        def notas_de(modelo, c):
            assert c["grupo"] not in modelo
            return [c["nota"]] * len(c["base"])

        t = vf.avaliar(clipes, treinar, notas_de, 0.6)
        self.assertEqual(len(treinos), 5)
        self.assertEqual(t[(0.3, 0.5)]["nova"], [10, 10])
        self.assertEqual(t[(0.3, 0.5)]["falsas"], [0, 5])
        self.assertEqual(t[(0.4, 0.5)]["nova"], [10, 10])
        # a referência (detector de hoje) não treina nada: nota = base, nos mesmos clipes
        self.assertEqual(len(treinos), 5)
        self.assertEqual(t[("base", 0.5)]["nova"], [0, 10])    # a base 0,4 < 0,5 NÃO dispara: 0 de 10
        self.assertEqual(t[("base", 0.5)]["falsas"], [5, 5])   # a base 0,6 dispara nas 5 falsas


class TestEscolher(unittest.TestCase):
    """Portão D17, revisado em 15/10: a referência é o detector de hoje (só a base, nota 0,5/0,6/0,7) nos MESMOS
    clipes, não números absolutos de contagens ao vivo. Medido: base 28/31 falsas, 104/105 verdadeiras, 30/60 antigas."""

    def tabela(self, nova, falsas, verd=(104, 105), hist=(30, 60), base=None):
        base = dict({"falsas": (28, 31), "verdadeiras": (104, 105), "historico": (30, 60)}, **(base or {}))
        cats = {"nova": nova, "falsas": falsas, "verdadeiras": verd, "historico": hist}
        t = {(lv, lp): {k: list(v) for k, v in cats.items()}
             for lv in vf.LIMIARES_VERIFICADOR for lp in vf.LIMIARES_PALAVRA}
        for lp in vf.LIMIARES_PALAVRA:
            t[("base", lp)] = {k: list(v) for k, v in base.items()}
        return t

    def test_aprova_quando_cumpre_tudo(self):
        ok, combo, textos, falhou = vf.escolher(self.tabela((9, 10), (3, 31), hist=(39, 60)), {})
        self.assertTrue(ok); self.assertIsNone(falhou)
        self.assertIn("disparos falsos que sobraram: 3 de 31 (hoje 28)", textos)

    def test_o_portao_antigo_reprovava_a_tabela_de_15_10_e_o_novo_aprova(self):
        # avaliação prévia (0,2; 0,6): falsas 2/31, verdadeiras 101/105, histórico 39/60; base 28/104/30
        ok, _, _, falhou = vf.escolher(self.tabela((9, 10), (2, 31), verd=(101, 105), hist=(39, 60)), {})
        self.assertTrue(ok, falhou)

    def test_prefere_o_limiar_mais_alto_entre_os_que_passam(self):
        # lv alto só filtra o que a base já aceita: não cria disparos novos (ponto cego medido por quem revisou)
        ok, (lv, lp), _, _ = vf.escolher(self.tabela((9, 10), (3, 31), hist=(39, 60)), {})
        self.assertEqual(lv, max(vf.LIMIARES_VERIFICADOR))

    def test_reprova_com_o_primeiro_criterio_que_falhou(self):
        ok, _, _, falhou = vf.escolher(self.tabela((7, 10), (3, 31), hist=(39, 60)), {})
        self.assertFalse(ok)
        self.assertEqual(falhou, "tentativas da pessoa nova aceitas: 7 de 10")

    def test_falsas_relativas_a_base(self):
        # base aceita 28; o verificador pode deixar passar no máximo 20% disso (5)
        self.assertTrue(vf.escolher(self.tabela((9, 10), (5, 31), hist=(39, 60)), {})[0])
        ok, _, _, falhou = vf.escolher(self.tabela((9, 10), (6, 31), hist=(39, 60)), {})
        self.assertFalse(ok); self.assertIn("disparos falsos", falhou)

    def test_verdadeiras_perdem_no_maximo_a_tolerancia(self):
        self.assertTrue(vf.escolher(self.tabela((9, 10), (3, 31), verd=(101, 105), hist=(39, 60)), {})[0])  # base 104 − 3
        ok, _, _, falhou = vf.escolher(self.tabela((9, 10), (3, 31), verd=(100, 105), hist=(39, 60)), {})
        self.assertFalse(ok); self.assertIn("verdadeiras", falhou)

    def test_a_referencia_e_sempre_o_detector_de_hoje_nao_o_do_mesmo_limiar(self):
        # 15/10: com a base medida em cada limiar da palavra, o 0,7 (base 90) passava perdendo 4 chamadas verdadeiras
        # em relação à produção (0,5; base 104). A referência é a de produção.
        t = self.tabela((9, 10), (3, 31), verd=(100, 105), hist=(39, 60))
        for lv in vf.LIMIARES_VERIFICADOR:
            t[(lv, 0.5)]["verdadeiras"] = [97, 105]; t[(lv, 0.6)]["verdadeiras"] = [97, 105]
        t[("base", 0.7)]["verdadeiras"] = [90, 105]
        ok, (lv, lp), _, falhou = vf.escolher(t, {})
        self.assertFalse(ok); self.assertIn("verdadeiras", falhou)

    def test_historico_nao_pode_ficar_abaixo_da_base(self):
        ok, _, _, falhou = vf.escolher(self.tabela((9, 10), (3, 31), hist=(29, 60)), {})
        self.assertFalse(ok); self.assertIn("antigas", falhou)
        self.assertTrue(vf.escolher(self.tabela((9, 10), (3, 31), hist=(30, 60)), {})[0])

    def test_pessoa_ja_cadastrada_nao_pode_perder_mais_de_uma(self):
        t = self.tabela((9, 10), (3, 31), hist=(39, 60))
        for k, cats in t.items():
            if k[0] != "base":
                cats["pessoa:jose"] = [7, 10]
        ok, _, _, falhou = vf.escolher(t, {"jose": 9})
        self.assertFalse(ok); self.assertIn("jose", falhou)

    def test_fala_livre_nao_pode_ganhar_disparos(self):
        t = self.tabela((9, 10), (3, 31), hist=(39, 60))
        for k, cats in t.items():
            cats["fala_livre"] = [0, 1] if k[0] == "base" else [2, 1]
        ok, _, _, falhou = vf.escolher(t, {})
        self.assertFalse(ok); self.assertIn("fala livre", falhou)


class TestApoio(unittest.TestCase):
    def test_ouviu_mal(self):
        self.assertEqual(vf.ouviu_mal([[0.1, 0.2], [0.4], [0.0]]), 2)

    def test_dez_tentativas(self):
        r = vf.recortes_tentativas(65.5)
        self.assertEqual(len(r), 10)
        self.assertEqual(r[0], (1.7, 7.3))
        self.assertEqual(r[5][0], round(1.5 + 30 + 4.9 + 0.2, 2))

    def test_vinte_tentativas_cabem_na_gravacao(self):
        r = vf.recortes_tentativas(126.0)
        self.assertEqual(len(r), 20)
        self.assertLessEqual(r[-1][1], 126.0)
        self.assertTrue(all(a < b for a, b in r))
