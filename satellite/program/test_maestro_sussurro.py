"""Spec 2026-10-20 D1/D5/D6/D7: conversa sussurrada no maestro.
cd /homeassistant/tools/satelite/programa && python3 -m unittest test_maestro_sussurro -v"""
import unittest
from maestro import Ajustes, Maestro

SUSSURRO = {"whisper": True}


def registro(acoes):
    return next(a[1] for a in acoes if a[0] == "registrar")


class Base(unittest.TestCase):
    def setUp(self):
        self.m = Maestro(Ajustes())

    def sussurrada_ate_calada(self):
        acoes = self.m.evento("palavra", 0.0, SUSSURRO)
        for nome, t in (("conectou", 0.5), ("fala_comecou", 1.0), ("fala_parou", 2.0)):
            acoes += self.m.evento(nome, t)
        return acoes + self.m.evento("tique", 11.0)


class TestAbertura(Base):
    def test_palavra_sussurrada_conecta_com_whisper(self):
        acoes = self.m.evento("palavra", 0.0, SUSSURRO)
        self.assertIn(("conectar", {"whisper": True}), acoes)
        self.assertNotIn(("som", "inicio"), acoes)

    def test_palavra_normal_igual_a_hoje(self):
        for valor in ({"whisper": False}, None):
            m = Maestro(Ajustes())
            acoes = m.evento("palavra", 0.0, valor)
            self.assertIn(("conectar", None), acoes, valor)
            self.assertIn(("som", "inicio"), acoes, valor)

    def test_botao_abre_normal(self):
        acoes = self.m.evento("botao", 0.0)
        self.assertIn(("som", "inicio"), acoes)
        self.assertIn(("conectar", None), acoes)

    def test_erro_toca_em_sussurro(self):
        self.m.evento("palavra", 0.0, SUSSURRO)
        self.assertIn(("som", "erro"), self.m.evento("falha_conexao", 1.0))


class TestFechar(Base):
    def test_fechar_sussurrada_sem_som_de_fim(self):
        acoes = self.sussurrada_ate_calada()
        self.assertNotIn(("som", "fim"), acoes)
        r = registro(acoes)
        self.assertEqual((r["como_fechou"], r["whisper"]), ("calada", True))

    def test_registro_normal_nao_tem_whisper(self):
        self.m.evento("palavra", 0.0)
        self.m.evento("conectou", 0.5)
        self.m.evento("fala_comecou", 1.0)
        self.m.evento("fala_parou", 2.0)
        self.assertNotIn("whisper", registro(self.m.evento("tique", 11.0)))


class TestAvisos(Base):
    def test_avisos_ao_bot(self):
        self.m.evento("palavra", 0.0, SUSSURRO)
        self.assertIn(("avisar_bot", "speech_started"), self.m.evento("conectou", 0.5))
        self.assertIn(("avisar_bot", "speech_started"), self.m.evento("fala_comecou", 1.0))
        self.assertIn(("avisar_bot", "speech_stopped"), self.m.evento("fala_parou", 2.0))

    def test_conversa_normal_sem_avisos(self):
        self.m.evento("palavra", 0.0, {"whisper": False})
        for nome, t in (("conectou", 0.5), ("fala_comecou", 1.0), ("fala_parou", 2.0)):
            acoes = self.m.evento(nome, t)
            self.assertFalse([a for a in acoes if a[0] == "avisar_bot"], nome)


class TestFalaEmAndamento(Base):
    def test_conversa_sussurrada_comeca_com_fala_em_andamento(self):
        """A frase inteira costuma acabar antes de a conexão abrir: sem isso nem a espera inicial (4 s) nem o fim da
        fala existiriam, e o Live ficaria sem activity_end."""
        self.m.evento("palavra", 0.0, SUSSURRO)
        self.m.evento("conectou", 1.0)
        self.assertEqual(self.m.evento("tique", 6.0), [])  # passou dos 4 s da espera inicial e não fechou
        self.assertEqual(self.m.estado, "ouvindo")
        self.assertIn(("avisar_bot", "speech_stopped"), self.m.evento("fala_parou", 7.0))
        acoes = self.m.evento("tique", 16.0)
        self.assertEqual(registro(acoes)["como_fechou"], "calada")

    def test_conversa_normal_continua_fechando_sem_fala(self):
        self.m.evento("palavra", 0.0)
        self.m.evento("conectou", 1.0)
        self.assertEqual(registro(self.m.evento("tique", 6.0))["como_fechou"], "sem_fala")


class TestEscopo(Base):
    def test_reabrir_mantem_whisper(self):
        self.m.evento("palavra", 0.0, SUSSURRO)
        self.m.evento("conectou", 0.5)
        self.m.evento("fala_comecou", 1.0); self.m.evento("fala_parou", 2.0)
        self.m.evento("ela_comecou", 3.0)
        self.m.evento("cerebro_pendente", 3.5, 1)
        acoes = self.m.evento("botao", 4.0)  # corta a resposta (CORTADA)
        self.assertEqual(self.m.estado, "cortada")
        self.assertNotIn(("som", "fim"), acoes)
        acoes = self.m.evento("palavra", 5.0, {"whisper": False})
        self.assertNotIn(("som", "inicio"), acoes)
        self.assertNotIn(("som", "fim"), self.m.evento("ela_parou", 6.0))

    def test_entrega_depois_de_sussurro_tem_sons(self):
        self.sussurrada_ate_calada()
        self.assertEqual(self.m.estado, "parada")
        como, acoes = self.m.campainha("e1", 20.0)
        self.assertEqual(como, "vou_abrir")
        self.assertIn(("som", "lembrete"), acoes)
        self.assertIn(("conectar", {"entrega": "e1"}), acoes)
        # e o som de fim da conversa de entrega volta a tocar
        self.m.evento("conectou", 20.5)
        fim = self.m.evento("botao", 21.0)
        self.assertIn(("som", "fim"), fim)

    def test_botao_depois_de_sussurro_abre_normal(self):
        self.sussurrada_ate_calada()
        acoes = self.m.evento("botao", 30.0)
        self.assertIn(("som", "inicio"), acoes)
        self.assertNotIn(("avisar_bot", "speech_started"), self.m.evento("conectou", 30.5))


if __name__ == "__main__":
    unittest.main()


class TestCorridaDaConexao(Base):
    """Revisão final (20/10): a frase sussurrada costuma acabar ANTES de a conexão abrir. O aviso de fim da fala não
    pode se perder nem ser apagado pelo `conectou`; e nada vai ao bot antes de conectar (o canal não está aberto)."""
    def test_fim_da_fala_antes_de_conectar(self):
        self.m.evento("palavra", 0.0, SUSSURRO)
        antes = self.m.evento("fala_parou", 1.0)
        self.assertFalse([a for a in antes if a[0] == "avisar_bot"])
        acoes = self.m.evento("conectou", 1.5)
        avisos = [a for a in acoes if a[0] == "avisar_bot"]
        self.assertEqual(avisos, [("avisar_bot", "speech_started"), ("avisar_bot", "speech_stopped")])
        self.assertIsNotNone(self.m._prazo)  # o prazo de resposta do fala_parou continua valendo
        self.assertEqual(registro(self.m.evento("tique", 40.0))["whisper"], True)  # e a conversa fecha

    def test_fala_ainda_em_andamento_ao_conectar(self):
        self.m.evento("palavra", 0.0, SUSSURRO)
        acoes = self.m.evento("conectou", 1.0)
        self.assertEqual([a for a in acoes if a[0] == "avisar_bot"], [("avisar_bot", "speech_started")])

    def test_nada_ao_bot_antes_de_conectar(self):
        self.m.evento("palavra", 0.0, SUSSURRO)
        for nome, t in (("fala_comecou", 0.3), ("fala_parou", 0.8)):
            self.assertFalse([a for a in self.m.evento(nome, t) if a[0] == "avisar_bot"], nome)
