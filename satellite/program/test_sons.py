"""Testes dos sons: cd /homeassistant/tools/satelite/programa && python3 -m unittest test_sons -v"""
import os, tempfile, unittest, wave
import sons


def duracao(p):
    with wave.open(p) as w:
        return w.getnframes() / w.getframerate()


class TestSonsNovos(unittest.TestCase):
    """Spec 2026-10-12 D12: bip de volume, alarme de timer e aviso de lembrete, distintos dos atuais."""

    def test_gera_os_tres_sons_novos(self):
        with tempfile.TemporaryDirectory() as d:
            feitos = sons.gerar(d)
            for nome in ("volume_a", "alarme_a", "lembrete_a"):
                self.assertTrue(os.path.exists(feitos[nome]), nome)

    def test_bip_de_volume_e_curto(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertLess(duracao(sons.gerar(d)["volume_a"]), 0.15)

    def test_alarme_tem_uma_volta_de_1_a_2_s(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertTrue(1.0 <= duracao(sons.gerar(d)["alarme_a"]) <= 2.0)

    def test_novos_nao_repetem_notas_dos_atuais(self):
        atuais = {tuple(n) for v in ("inicio", "fim", "erro") for n in sons.VARIANTES[v].values()}
        for v in ("volume", "alarme", "lembrete"):
            self.assertNotIn(tuple(sons.VARIANTES[v]["a"]), atuais)
