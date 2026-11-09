"""Maestro e a rede esgotada (spec 2026-11-08, rede persistente): luz de erro fixa em repouso, som de erro no mudo,
o satelite nunca fica mudo por causa dela.
cd /homeassistant/tools/satelite/programa && python3 -m unittest test_maestro_rede -v"""
import unittest
from maestro import Ajustes, Maestro


class Base(unittest.TestCase):
    def setUp(self):
        self.m = Maestro(Ajustes())

    def abrir_conversa(self, t=0.0):
        self.m.evento("palavra", t)
        self.m.evento("conectou", t + 0.5)
        self.assertEqual(self.m.estado, "ouvindo")


class TestRedeEsgotada(Base):
    def test_exhausted_lights_error_in_parada(self):
        self.assertEqual(self.m.evento("rede_esgotada", 0.0, True), [("luz", "rede_esgotada")])
        self.assertTrue(self.m.rede_esgotada)

    def test_same_value_twice_does_nothing(self):
        self.m.evento("rede_esgotada", 0.0, True)
        self.assertEqual(self.m.evento("rede_esgotada", 1.0, True), [])
        self.assertEqual(self.m.evento("rede_esgotada", 1.0, False), [("luz", "parada")])
        self.assertEqual(self.m.evento("rede_esgotada", 2.0, False), [])

    def test_exhausted_overrides_dnd_light(self):
        self.m.evento("nao_perturbe", 0.0, True)
        self.assertEqual(self.m._luz_do_estado(), "nao_perturbe")
        self.assertEqual(self.m.evento("rede_esgotada", 1.0, True), [("luz", "rede_esgotada")])
        self.assertEqual(self.m._luz_do_estado(), "rede_esgotada")

    def test_recovered_returns_to_rest_light(self):
        self.m.evento("rede_esgotada", 0.0, True)
        self.assertEqual(self.m.evento("rede_esgotada", 1.0, False), [("luz", "parada")])
        self.m.evento("nao_perturbe", 2.0, True)
        self.m.evento("rede_esgotada", 3.0, True)
        self.assertEqual(self.m.evento("rede_esgotada", 4.0, False), [("luz", "nao_perturbe")])

    def test_exhausted_during_conversation_lights_after_close(self):
        self.abrir_conversa()
        self.assertEqual(self.m.evento("rede_esgotada", 1.0, True), [])
        self.assertEqual(self.m._luz_do_estado(), "ouvindo")
        acoes = self.m.evento("botao", 2.0, "vermelho")  # fecha a conversa
        self.assertIn(("luz", "rede_esgotada"), acoes)
        self.assertEqual(self.m.estado, "parada")

    def test_no_light_while_physically_muted(self):
        self.m.evento("mudo", 0.0, True)
        self.assertEqual(self.m.evento("rede_esgotada", 1.0, True), [])
        self.assertTrue(self.m.rede_esgotada)

    def test_mute_during_exhausted_plays_error_and_unmutes_on_recovery(self):
        self.m.evento("rede_esgotada", 0.0, True)
        acoes = self.m.evento("mudo", 1.0, True)
        self.assertIn(("som", "erro"), acoes)
        self.assertTrue(self.m._mudo_na_falha)
        # rede volta com o mudo ainda marcado: desfaz o mudo, sem escrever luz por cima do mudo fisico
        acoes = self.m.evento("rede_esgotada", 2.0, False)
        self.assertEqual(acoes, [("desfazer_mudo", None)])
        self.assertFalse(self.m._mudo_na_falha)

    def test_mute_without_exhausted_is_unchanged(self):
        acoes = self.m.evento("mudo", 0.0, True)
        self.assertNotIn(("som", "erro"), acoes)
        self.assertFalse(self.m._mudo_na_falha)
        self.m.evento("rede_esgotada", 1.0, True)
        self.m.evento("rede_esgotada", 2.0, False)  # mudo do usuario, antes da falha: nao desfaz
        self.assertFalse(self.m._mudo_na_falha)

    def test_user_unmutes_before_recovery_clears_marker(self):
        self.m.evento("rede_esgotada", 0.0, True)
        self.m.evento("mudo", 1.0, True)
        self.m.evento("mudo", 2.0, False)
        self.assertFalse(self.m._mudo_na_falha)
        self.assertEqual(self.m.evento("rede_esgotada", 3.0, False), [("luz", "parada")])

    def test_recovery_after_user_unmuted_does_not_undo_new_user_mute(self):
        self.m.evento("rede_esgotada", 0.0, True)
        self.m.evento("mudo", 1.0, True)
        self.m.evento("mudo", 2.0, False)
        self.m.evento("mudo", 3.0, True)  # ainda esgotada: toca erro e marca de novo
        self.assertTrue(self.m._mudo_na_falha)

    def test_publico_reports_net_exhausted(self):
        self.assertIs(self.m.publico()["net_exhausted"], False)
        self.m.evento("rede_esgotada", 0.0, True)
        self.assertIs(self.m.publico()["net_exhausted"], True)

    def test_flag_survives_zerar(self):
        self.m.evento("rede_esgotada", 0.0, True)
        self.m._zerar()
        self.assertTrue(self.m.rede_esgotada)


if __name__ == "__main__":
    unittest.main()
