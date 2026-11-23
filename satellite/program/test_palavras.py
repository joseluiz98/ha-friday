"""cd satellite/program && python3 -m unittest test_palavras -v"""
import os, tempfile, unittest
import palavras as pw

EXTRA = {"nome": "sexta_feira", "arquivo": "/home/jose/modelos/sexta_feira.onnx", "limiar": 0.5, "modo": "ativa"}


class Sons(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory(); self.addCleanup(self._t.cleanup)
        self.d = self._t.name

    def fala(self, palavra):
        open(pw.arquivo_da_intro(palavra, self.d), "wb").close()


class TestNomes(unittest.TestCase):
    def test_nome_falado(self):
        self.assertEqual(pw.nome_falado("friday"), "Friday")
        self.assertEqual(pw.nome_falado("sexta_feira"), "Sexta-feira")

    def test_janela_da_principal_nao_muda(self):
        self.assertEqual(pw.nome_janela("friday", 3), "tentativa_03")
        self.assertEqual(pw.nome_janela("sexta_feira", 3), "sexta_feira__tentativa_03")

    def test_palavra_da_janela(self):
        self.assertEqual(pw.palavra_da_janela("tentativa_03"), "friday")
        self.assertEqual(pw.palavra_da_janela("sexta_feira__tentativa_03"), "sexta_feira")
        self.assertEqual(pw.palavra_da_janela("fala_livre"), "friday")

    def test_verificador_de_cada_palavra(self):
        self.assertEqual(pw.verificador_de("friday"), "/home/jose/modelos/friday_verificador.pkl")
        self.assertEqual(pw.verificador_de("sexta_feira"), "/home/jose/modelos/sexta_feira_verificador.pkl")

    def test_todas_junta_principal_e_extras(self):
        self.assertEqual([p["nome"] for p in pw.todas({"palavras_extras": [EXTRA]})], ["friday", "sexta_feira"])
        self.assertEqual([p["nome"] for p in pw.todas({})], ["friday"])


class TestEscolher(Sons):
    cfg = {"palavras_extras": [EXTRA]}

    def test_so_entra_palavra_com_fala_gravada(self):
        self.fala("friday")
        r = pw.escolher(self.cfg, self.d)
        self.assertEqual(r, {"gravar": ["friday"], "puladas": ["sexta_feira"]})

    def test_todas_com_fala(self):
        self.fala("friday"); self.fala("sexta_feira")
        self.assertEqual(pw.escolher(self.cfg, self.d)["gravar"], ["friday", "sexta_feira"])

    def test_quem_ja_tem_a_friday_grava_so_a_que_falta(self):
        self.fala("friday"); self.fala("sexta_feira")
        self.assertEqual(pw.escolher(self.cfg, self.d, ["friday"])["gravar"], ["sexta_feira"])

    def test_quem_tem_todas_regrava_todas(self):
        self.fala("friday"); self.fala("sexta_feira")
        self.assertEqual(pw.escolher(self.cfg, self.d, ["friday", "sexta_feira"])["gravar"], ["friday", "sexta_feira"])

    def test_satelite_sem_extras(self):
        self.fala("friday")
        self.assertEqual(pw.escolher({}, self.d), {"gravar": ["friday"], "puladas": []})


class TestConfig(unittest.TestCase):
    def test_principal_e_extra_aprovadas(self):
        cfg = {"palavras_extras": [dict(EXTRA)], "limiar_verificador": 0.3}
        m = pw.mudancas_de_config(cfg, {"friday": {"limiar_palavra": 0.6, "limiar_verificador": 0.3},
                                        "sexta_feira": {"limiar_palavra": 0.7, "limiar_verificador": 0.3}})
        self.assertEqual(m["verificador"], pw.VERIFICADOR_PRINCIPAL)
        self.assertEqual(m["limiar_palavra"], 0.6)
        self.assertEqual(m["limiar_verificador"], 0.3)
        self.assertEqual(m["palavras_extras"][0]["verificador"], "/home/jose/modelos/sexta_feira_verificador.pkl")
        self.assertEqual(m["palavras_extras"][0]["limiar"], 0.7)

    def test_so_a_extra_nao_mexe_na_principal(self):
        m = pw.mudancas_de_config({"palavras_extras": [dict(EXTRA)]},
                                  {"sexta_feira": {"limiar_palavra": 0.5, "limiar_verificador": 0.4}})
        self.assertNotIn("verificador", m); self.assertNotIn("limiar_palavra", m)
        self.assertEqual(m["limiar_verificador"], 0.4)

    def test_nao_altera_a_config_original(self):
        cfg = {"palavras_extras": [dict(EXTRA)]}
        pw.mudancas_de_config(cfg, {"sexta_feira": {"limiar_palavra": 0.7, "limiar_verificador": 0.3}})
        self.assertNotIn("verificador", cfg["palavras_extras"][0])

    def test_limiar_do_verificador_em_uso(self):
        self.assertIsNone(pw.limiar_do_verificador({"limiar_verificador": 0.3}))
        self.assertEqual(pw.limiar_do_verificador({"verificador": "/x.pkl", "limiar_verificador": 0.5}), 0.5)
        self.assertEqual(pw.limiar_do_verificador({"limiar_verificador": 0.4, "palavras_extras": [{"verificador": "/y.pkl"}]}), 0.4)


if __name__ == "__main__":
    unittest.main()
