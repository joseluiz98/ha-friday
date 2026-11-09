"""Spec 2026-10-15 D15–D20: estado CADASTRO do maestro.
cd /homeassistant/tools/satelite/programa && python3 -m unittest test_maestro_cadastro -v"""
import unittest
from maestro import Ajustes, Maestro

BIA = {"nome": "Bia", "id": "bia"}


def registro(acoes):
    return next(a[1] for a in acoes if a[0] == "registrar")


class Base(unittest.TestCase):
    def setUp(self):
        self.m = Maestro(Ajustes())

    def agendado(self):
        self.m.evento("palavra", 0.0); self.m.evento("conectou", 0.5)
        self.m.evento("fala_comecou", 1.0); self.m.evento("fala_parou", 2.0)
        self.m.evento("ela_comecou", 3.0)
        self.m.evento("cadastro_agendado", 3.5, BIA)

    def em_cadastro(self):
        self.agendado()
        return self.m.evento("ela_parou", 5.0)


class TestCadastro(Base):
    def test_ela_parou_fecha_e_entra_em_cadastro(self):
        acoes = self.em_cadastro()
        self.assertIn(("cadastro", "iniciar"), acoes)
        self.assertIn(("desconectar", None), acoes)
        self.assertEqual(registro(acoes)["como_fechou"], "aprendizagem")
        self.assertEqual(self.m.estado, "cadastro")
        self.assertEqual(self.m.publico()["cadastro"], "Bia")

    def test_palavra_e_fala_sao_ignoradas(self):
        self.em_cadastro()
        for ev in ("palavra", "fala_comecou", "fala_parou", "ela_comecou", "tique"):
            self.assertEqual(self.m.evento(ev, 6.0), [], ev)
        self.assertEqual(self.m.estado, "cadastro")

    def test_botao_cancela(self):
        self.em_cadastro()
        acoes = self.m.evento("botao", 10.0)
        self.assertIn(("cadastro", "cancelar"), acoes)
        self.assertEqual((self.m.estado, self.m.publico()["cadastro"]), ("parada", None))

    def test_mudo_cancela(self):
        self.em_cadastro()
        self.assertIn(("cadastro", "cancelar"), self.m.evento("mudo", 10.0, True))

    def test_mudo_antes_de_comecar_cancela_o_agendamento(self):
        """Revisão final (I3): mudo enquanto ela confirma não pode gravar 2 min de silêncio."""
        self.agendado()
        self.m.evento("mudo", 4.0, True)
        acoes = self.m.evento("ela_parou", 5.0)
        self.assertNotIn(("cadastro", "iniciar"), acoes)
        self.assertIn(("cadastro", "cancelar_agendado"), acoes)
        self.assertNotEqual(self.m.estado, "cadastro")

    def test_fim_volta_a_parada(self):
        self.em_cadastro()
        acoes = self.m.evento("cadastro_fim", 130.0)
        self.assertNotIn("cadastro", [a[0] for a in acoes])
        self.assertEqual(self.m.estado, "parada")

    def test_timer_interrompe_e_o_alarme_assume(self):
        self.em_cadastro()
        acoes = self.m.evento("timer_venceu", 20.0, {"id": "t1", "nome": "arroz", "duracao_s": 60})
        self.assertIn(("cadastro", "interromper"), acoes)
        self.assertIn(("alarme", "tocar"), acoes)
        self.assertEqual(self.m.estado, "alarme")

    def test_timer_com_agendamento_cancela_o_agendamento(self):
        self.agendado()
        acoes = self.m.evento("timer_venceu", 4.0, {"id": "t1", "nome": None, "duracao_s": 60})
        self.assertIn(("cadastro", "cancelar_agendado"), acoes)
        self.assertNotIn(("cadastro", "iniciar"), acoes)
        self.assertEqual(self.m.estado, "alarme")

    def test_calada_com_agendamento_tambem_entra(self):
        self.m.evento("palavra", 0.0); self.m.evento("conectou", 0.5)
        self.m.evento("fala_comecou", 1.0); self.m.evento("fala_parou", 2.0)
        self.m.evento("cadastro_agendado", 2.5, BIA)
        acoes = self.m.evento("tique", 10.0)
        self.assertIn(("cadastro", "iniciar"), acoes)
        self.assertEqual(registro(acoes)["como_fechou"], "calada")

    def test_queda_cancela_o_agendamento(self):
        self.agendado()
        acoes = self.m.evento("caiu", 4.0)
        self.assertIn(("cadastro", "cancelar_agendado"), acoes)
        self.assertEqual((self.m.estado, self.m.publico()["cadastro"]), ("parada", None))

    def test_agendamento_com_a_conversa_parada_e_ignorado(self):
        self.assertEqual(self.m.evento("cadastro_agendado", 0.0, BIA), [])
        self.assertIsNone(self.m.publico()["cadastro"])

    def test_campainha_espera_na_fila(self):
        self.em_cadastro()
        self.assertEqual(self.m.campainha("e1abcdef", 6.0), ("na_fila", []))
        self.m.evento("cadastro_fim", 60.0)   # a entrega espera até 90 s na fila (fila_entrega_max_s)
        self.assertIn(("conectar", {"entrega": "e1abcdef"}), self.m.evento("tique", 60.2))
