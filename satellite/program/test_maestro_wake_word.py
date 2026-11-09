"""28/10/2026: a palavra que abriu a conversa vai na oferta ao bot (aparece no Jarvis Logbook, só no 1º turno).
cd /homeassistant/tools/satelite/programa && python3 -m unittest test_maestro_wake_word -v"""
import unittest
from maestro import Ajustes, Maestro


def conectar(acoes):
    return next(a[1] for a in acoes if a[0] == "conectar")


class TestWakeWord(unittest.TestCase):
    def setUp(self):
        self.m = Maestro(Ajustes())

    def test_palavra_vai_no_conectar(self):
        acoes = self.m.evento("palavra", 0.0, {"whisper": False, "wake_word": "sexta_feira"})
        self.assertEqual(conectar(acoes), {"wake_word": "sexta_feira"})

    def test_sussurro_e_palavra_juntos(self):
        acoes = self.m.evento("palavra", 0.0, {"whisper": True, "wake_word": "friday"})
        self.assertEqual(conectar(acoes), {"whisper": True, "wake_word": "friday"})

    def test_sem_palavra_igual_a_antes(self):
        self.assertIsNone(conectar(self.m.evento("palavra", 0.0, {"whisper": False})))

    def test_botao_nao_tem_palavra(self):
        self.assertIsNone(conectar(self.m.evento("botao", 0.0)))

    def test_palavra_encerra_alarme_e_abre_com_ela(self):
        self.m.estado, self.m._alarme = "alarme", {"timer": {"id": "t1"}, "inicio": 0.0, "atendido": False}
        self.m.mudo = False
        acoes = self.m.evento("palavra", 1.0, {"wake_word": "friday"})
        self.assertEqual(conectar(acoes), {"wake_word": "friday"})


if __name__ == "__main__":
    unittest.main()
