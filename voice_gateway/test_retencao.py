"""cd /srv/friday/app && python3 -m unittest test_retencao -v"""
import unittest
from retencao import RetencaoDeAudio


class TestRetencao(unittest.TestCase):
    def test_segura_ate_pronto_e_libera_na_ordem(self):
        r = RetencaoDeAudio(max_quadros=100)
        self.assertEqual(r.entrar("a", pronto=False), [])
        self.assertEqual(r.entrar("b", pronto=False), [])
        self.assertEqual(r.liberar_se_pronto(False), [])
        self.assertEqual(r.liberar_se_pronto(True), ["a", "b"])
        self.assertEqual(r.entrar("c", pronto=True), ["c"])

    def test_pronto_com_fila_libera_antes_do_novo(self):
        r = RetencaoDeAudio(max_quadros=100)
        r.entrar("a", pronto=False)
        self.assertEqual(r.entrar("b", pronto=True), ["a", "b"])

    def test_teto_descarta_o_mais_antigo(self):
        r = RetencaoDeAudio(max_quadros=2)
        for q in "abc":
            r.entrar(q, pronto=False)
        self.assertEqual(r.liberar_se_pronto(True), ["b", "c"])

    def test_depois_de_pronto_nao_retem_mais(self):
        r = RetencaoDeAudio(max_quadros=100)
        r.liberar_se_pronto(True)
        self.assertEqual(r.entrar("x", pronto=False), ["x"])  # queda momentânea depois de pronto: passa direto


if __name__ == "__main__":
    unittest.main()
