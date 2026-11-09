import unittest
from replay_sexta import simular


class TestSimular(unittest.TestCase):
    def test_um_quadro_nao_dispara(self):
        self.assertEqual(simular([[10, .9, .9]]), [])

    def test_dois_seguidos_disparam_uma_vez(self):
        self.assertEqual(simular([[10, .9, .9], [11, .9, .9], [12, .9, .9]]), [0.88])

    def test_voz_baixa_nao_conta(self):
        self.assertEqual(simular([[10, .9, .3], [11, .9, .3]]), [])

    def test_rearma_depois_de_lacuna(self):
        self.assertEqual(len(simular([[1, .9, .9], [2, .9, .9], [50, .9, .9], [51, .9, .9]])), 2)

    def test_nao_rearma_acima_de_0_2(self):
        self.assertEqual(len(simular([[1, .9, .9], [2, .9, .9], [3, .3, .9], [4, .9, .9], [5, .9, .9]])), 1)


if __name__ == "__main__":
    unittest.main()
