import unittest

import neg_rules as n


class TestAceita(unittest.TestCase):
    def test_transcricao_fiel(self):
        self.assertTrue(n.aceita("Quarta-feira", "Quarta-feira."))
        self.assertTrue(n.aceita("Terça-feira", "terca feira!"))

    def test_virou_a_propria_palavra(self):
        for t in ("Sexta-feira", "sexta feira.", "Esta feira", "Nesta feira", "Testa feira", "sesta-feira"):
            self.assertFalse(n.aceita("Quinta-feira", t), t)

    def test_quinta_nao_casa_por_acaso(self):
        self.assertFalse(n.aceita("Festa", "Obrigado por assistir"))

    def test_vazia(self):
        self.assertFalse(n.aceita("Festa", ""))

    def test_sexta_sozinha_vale(self):
        self.assertTrue(n.aceita("Sexta passada", "Sexta passada."))


if __name__ == "__main__":
    unittest.main()
