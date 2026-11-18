"""Taxa da captura publicada no /status (spec 2026-11-17 xvf3800-support, Task 5): amostras recebidas por janela de
10 s sobre as amostras do tempo real. O agente decide (vigia da USB); aqui só se mede.
cd satellite/program && python3 -m unittest -q test_taxa_captura"""
import unittest

from taxa_captura import TaxaCaptura

PEDACO = 2560  # amostras por leitura da captura (160 ms a 16 kHz)


def alimentar(t, inicio, fim, passo_s, n, tempo_real_s=0.16, agora0=0.0):
    """Alimenta `n` amostras a cada `passo_s` (relógio do teste) entre `inicio` e `fim`."""
    x = inicio
    while x <= fim + 1e-9:
        t.alimentar(n, agora0 + x)
        x += passo_s


class TestTaxaCaptura(unittest.TestCase):
    def test_none_before_first_window(self):
        t = TaxaCaptura()
        self.assertIsNone(t.atual())
        alimentar(t, 0.0, 5.0, 0.16, PEDACO)
        self.assertIsNone(t.atual())
        self.assertIsNone(t.ts)

    def test_rate_one_when_realtime(self):
        t = TaxaCaptura()
        alimentar(t, 0.0, 10.4, 0.16, PEDACO)
        self.assertAlmostEqual(t.atual(), 1.0, delta=0.01)

    def test_rate_low_when_slow(self):
        """Chega um quinto do tempo real (o que a USB do XVF fez em 17/11)."""
        t = TaxaCaptura()
        alimentar(t, 0.0, 10.4, 0.8, PEDACO)
        self.assertAlmostEqual(t.atual(), 0.2, delta=0.02)

    def test_each_window_is_independent(self):
        t = TaxaCaptura()
        alimentar(t, 0.0, 10.4, 0.16, PEDACO)
        self.assertAlmostEqual(t.atual(), 1.0, delta=0.01)
        alimentar(t, 10.56, 30.0, 0.8, PEDACO)  # fica lenta: a janela nova não herda a boa
        self.assertLess(t.atual(), 0.3)

    def test_ts_is_the_wall_clock_when_the_window_closes(self):
        t = TaxaCaptura(relogio=lambda: 1_700_000_000.0)
        alimentar(t, 0.0, 10.4, 0.16, PEDACO)
        self.assertEqual(t.ts, 1_700_000_000.0)

    def test_custom_window_and_rate(self):
        t = TaxaCaptura(janela_s=2.0, taxa_hz=48000)
        alimentar(t, 0.0, 2.2, 0.1, 4800)
        self.assertAlmostEqual(t.atual(), 1.0, delta=0.01)


if __name__ == "__main__":
    unittest.main()


class TestIdade(unittest.TestCase):
    def test_age_is_monotonic_not_wall_clock(self):
        # review I5 (17/11): the NTP step after boot moved the wall clock and looked like a stopped capture
        m = {"t": 100.0}
        t = TaxaCaptura(relogio=lambda: m["t"])
        self.assertIsNone(t.idade())
        t.alimentar(160, 0.0); t.alimentar(160000, 10.0)
        m["t"] = 103.0
        self.assertAlmostEqual(t.idade(), 3.0)
