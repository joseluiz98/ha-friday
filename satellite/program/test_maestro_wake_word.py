"""28/10/2026: a palavra que abriu a conversa vai na oferta ao bot (aparece no Jarvis Logbook, só no 1º turno).
cd /homeassistant/tools/satelite/programa && python3 -m unittest test_maestro_wake_word -v"""
import copy
import unittest
from maestro import (ALARME, AGUARDANDO, CADASTRO, CONECTANDO, CORTADA, OUVINDO, PARADA, TESTE_PALAVRA, Ajustes,
                     Maestro)


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


class TestPalavraAgiria(unittest.TestCase):
    """Spec 2026-11-16 §5.1: o satélite só avisa o bot de uma palavra que o maestro de fato usaria. `palavra_agiria`
    é a mesma regra do `evento` (pura, sem mexer no estado)."""

    def maestro(self, estado, mudo, recado=False):
        m = Maestro(Ajustes(), mudo_inicial=mudo)
        m.estado = estado
        if estado in (CONECTANDO, OUVINDO, CORTADA, AGUARDANDO):
            m._abertura = {"inicio": 0.0, "como_abriu": "palavra"}
        if estado == ALARME:
            m._alarme = {"timer": {"id": "t1"}, "inicio": 0.0, "atendido": False}
        if estado == TESTE_PALAVRA:
            m._teste = {"contagem": 0}
        if estado == CADASTRO:
            m._cadastro = {"nome": "x"}
        m._recado = recado
        return m

    def test_palavra_agiria_matches_evento(self):
        estados = (PARADA, CONECTANDO, OUVINDO, CORTADA, AGUARDANDO, ALARME)
        for estado in estados:
            for mudo in (False, True, None):
                for recado in (False, True):
                    with self.subTest(estado=estado, mudo=mudo, recado=recado):
                        m = self.maestro(estado, mudo, recado)
                        previsto = m.palavra_agiria({"wake_word": "friday"})
                        antes = copy.deepcopy(m.__dict__)
                        self.assertEqual(m.__dict__, antes)  # pura
                        real = bool(copy.deepcopy(m).evento("palavra", 1.0, {"wake_word": "friday"}))
                        self.assertEqual(previsto, real)

    def test_abertas_so_agem_sobre_um_recado(self):
        for estado in (CONECTANDO, OUVINDO):
            self.assertFalse(self.maestro(estado, False).palavra_agiria(None))
            self.assertTrue(self.maestro(estado, False, recado=True).palavra_agiria(None))
            self.assertFalse(self.maestro(estado, True, recado=True).palavra_agiria(None))

    def test_parada_cortada_aguardando_agem_e_o_alarme_tambem(self):
        for estado in (PARADA, CORTADA, AGUARDANDO, ALARME):
            self.assertTrue(self.maestro(estado, False).palavra_agiria(None))
            self.assertFalse(self.maestro(estado, True).palavra_agiria(None))
            self.assertFalse(self.maestro(estado, None).palavra_agiria(None))

    def test_teste_e_cadastro_nunca_disputam(self):
        # o teste da palavra e a gravação são da Pi (nada de "quem ouviu melhor"): o evento é tratado como hoje
        for estado in (TESTE_PALAVRA, CADASTRO):
            for mudo in (False, True):
                self.assertFalse(self.maestro(estado, mudo).palavra_agiria({"wake_word": "friday"}))
