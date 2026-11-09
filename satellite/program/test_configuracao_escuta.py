"""cd /homeassistant/tools/satelite/programa && python3 -m unittest test_configuracao_escuta -v"""
import unittest
import configuracao as c


class TestChavesNovas(unittest.TestCase):
    def test_padroes(self):
        self.assertEqual(c.PADROES["espera_resposta_s"], 8)
        self.assertIsNone(c.PADROES["verificador"])
        self.assertEqual(c.PADROES["limiar_verificador"], 0.3)
        for k in ("aprendizagem_intro", "aprendizagem_mudar_lugar", "aprendizagem_fala_livre", "aprendizagem_fim",
                  "aprendizagem_cancelada"):
            self.assertEqual(c.PADROES["sons"][k], k + ".wav")

    def test_validacao(self):
        base = dict(c.PADROES)
        novo = c.validar_mudancas(base, {"espera_resposta_s": 6, "verificador": "/home/jose/modelos/v.pkl",
                                         "limiar_verificador": 0.2})
        self.assertEqual((novo["espera_resposta_s"], novo["limiar_verificador"]), (6, 0.2))
        self.assertIsNone(c.validar_mudancas(novo, {"verificador": None})["verificador"])
        for ruim in ({"espera_resposta_s": 1}, {"espera_resposta_s": 31}, {"verificador": ""},
                     {"verificador": 3}, {"limiar_verificador": 1.0}, {"espera_inicial_s": None}):
            with self.assertRaises(ValueError, msg=ruim):
                c.validar_mudancas(base, ruim)


class TestWhisperConfig(unittest.TestCase):
    def test_padrao(self):
        self.assertEqual(c.PADROES["whisper_min_ratio_db"], 5.5)

    def test_validacao(self):
        base = dict(c.PADROES)
        for ok in (None, 7.5, -10, 40):
            self.assertEqual(c.validar_mudancas(base, {"whisper_min_ratio_db": ok})["whisper_min_ratio_db"], ok)
        for ruim in (41, -11, "x", True):
            with self.assertRaises(ValueError, msg=ruim):
                c.validar_mudancas(base, {"whisper_min_ratio_db": ruim})
