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


class TestGarantir(unittest.TestCase):
    """18/11 (MK2): the image's sound snapshot lacked volume_a.wav, so the volume command failed and played the error
    tone. The program fills in only the generated sounds that are missing, never overwriting a file."""

    def test_fills_missing_and_keeps_existing(self):
        with tempfile.TemporaryDirectory() as d:
            erro = os.path.join(d, "erro_b.wav")
            with open(erro, "wb") as f:
                f.write(b"keep")
            feitos = sons.garantir(d)
            self.assertIn("volume_a", feitos)
            self.assertTrue(os.path.exists(os.path.join(d, "volume_a.wav")))
            with open(erro, "rb") as f:
                self.assertEqual(f.read(), b"keep")
            self.assertEqual(sons.garantir(d), {})
