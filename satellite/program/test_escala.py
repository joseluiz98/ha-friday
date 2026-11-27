"""cd satellite/program && python3 -m unittest -q test_escala"""
import unittest
import escala as e

PLACAS = ("jabra", "xvf3800")


class TestEscala(unittest.TestCase):
    def test_grid(self):
        self.assertEqual([e.grade(k) for k in range(12)], [0, 9, 18, 27, 36, 45, 55, 64, 73, 82, 91, 100])

    def test_grid_percents_keep_todays_loudness(self):
        for placa in PLACAS:
            for k in range(12):
                p = e.grade(k)
                self.assertEqual((e.posicao_de_pct(p), e.alvo_db(p, placa), e.trim_db(p, placa)),
                                 (k, e.ANCORAS[placa][k], 0))

    def test_loudness_strictly_rises(self):
        for placa in PLACAS:
            vals = [e.NIVEIS[placa][e.nivel_de_pct(p, placa)] + e.trim_db(p, placa) for p in range(101)]
            self.assertTrue(all(b - a >= 0.1 for a, b in zip(vals, vals[1:])), placa)

    def test_jabra_levels_are_the_positions(self):
        self.assertTrue(all(e.nivel_de_pct(p, "jabra") == e.posicao_de_pct(p) for p in range(101)))
        self.assertTrue(all(-4.0 < e.trim_db(p, "jabra") <= 0 for p in range(101)))

    def test_xvf_uses_its_own_1db_levels(self):
        self.assertEqual(e.NIVEIS["xvf3800"], tuple(range(-60, 1)))
        self.assertTrue(all(-1.0 < e.trim_db(p, "xvf3800") <= 0 for p in range(101)))
        self.assertGreaterEqual(len({e.nivel_de_pct(p, "xvf3800") for p in range(101)}), 30)

    def test_percent_survives_db(self):
        for placa in PLACAS:
            for p in range(101):
                self.assertEqual(e.pct_de_db(e.alvo_db(p, placa), placa), p)

    def test_floor_and_tables(self):
        self.assertEqual((e.ANCORAS["jabra"][0], e.ANCORAS["xvf3800"][0]), (-36, -34))
        self.assertEqual(e.VOLUME_XVF, [26, 30, 33, 37, 40, 43, 47, 50, 53, 57, 58, 60])

    def test_bad_percent(self):
        for ruim in (-1, 101, True, 50.0, "50", None):
            with self.assertRaises(ValueError):
                e.posicao_de_pct(ruim)


if __name__ == "__main__":
    unittest.main()
