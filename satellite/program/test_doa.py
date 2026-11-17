"""DOA v0 / trava de feixe (spec 2026-11-17-xvf3800-support). python3 -m unittest -q test_doa"""
import math
import unittest

import doa


def rad(g):
    return math.radians(g)


class Link:
    def __init__(self, ok=True):
        self.escritas, self.ok, self.leituras = [], ok, {"AEC_FIXEDBEAMSONOFF": [1]}

    def write(self, nome, valores):
        self.escritas.append((nome, list(valores)))
        return self.ok

    def read(self, nome):
        return self.leituras.get(nome)


class TestDirecao(unittest.TestCase):
    def test_circular_mean_wraps(self):
        d = doa.Direcao()
        for t, g in ((9.0, 350), (9.3, 10), (9.6, 350), (9.9, 10)):
            d.alimentar(t, rad(g))
        theta = d.decidir(10.0)
        self.assertAlmostEqual(math.degrees(theta) % 360 if math.degrees(theta) % 360 < 180 else math.degrees(theta) % 360 - 360, 0, delta=0.5)

    def test_needs_three_speech_samples(self):
        d = doa.Direcao()
        d.alimentar(9.5, rad(90)); d.alimentar(9.6, rad(90)); d.alimentar(9.7, None)
        self.assertIsNone(d.decidir(10.0))
        d.alimentar(9.8, rad(90))
        self.assertAlmostEqual(math.degrees(d.decidir(10.0)), 90, delta=0.5)

    def test_needs_70_percent_within_30_degrees(self):
        d = doa.Direcao()
        for t, g in ((9.0, 90), (9.2, 95), (9.4, 270), (9.6, 260)):  # pessoa e TV disputando
            d.alimentar(t, rad(g))
        self.assertIsNone(d.decidir(10.0))

    def test_only_the_last_window_counts(self):
        d = doa.Direcao()
        for t in (5.0, 5.1, 5.2):  # velhas: fora de 1,5 s
            d.alimentar(t, rad(200))
        for t in (9.0, 9.2, 9.4):
            d.alimentar(t, rad(30))
        self.assertAlmostEqual(math.degrees(d.decidir(10.0)), 30, delta=0.5)

    def test_nan_counts_as_silence(self):
        d = doa.Direcao()
        for t in (9.0, 9.2, 9.4):
            d.alimentar(t, float("nan"))
        self.assertIsNone(d.decidir(10.0))


class TestTrava(unittest.TestCase):
    def test_lock_sequence(self):
        link = Link()
        t = doa.TravaFeixe(link, log=lambda m: None, relogio=lambda: 100.0)
        self.assertTrue(t.travar(rad(30)))
        self.assertEqual([e[0] for e in link.escritas],
                         ["AEC_FIXEDBEAMSAZIMUTH_VALUES", "AEC_FIXEDBEAMSONOFF", "AUDIO_MGR_OP_R"])
        self.assertAlmostEqual(link.escritas[0][1][0], rad(30)); self.assertAlmostEqual(link.escritas[0][1][1], rad(30))
        self.assertEqual(link.escritas[1][1], [1]); self.assertEqual(link.escritas[2][1], [7, 0])
        self.assertTrue(t.travado)

    def test_unlock_sequence(self):
        link = Link()
        t = doa.TravaFeixe(link, log=lambda m: None, relogio=lambda: 100.0)
        t.travar(rad(30)); link.escritas.clear()
        self.assertTrue(t.soltar())
        self.assertEqual(link.escritas, [("AEC_FIXEDBEAMSONOFF", [0]), ("AUDIO_MGR_OP_R", [7, 3])])
        self.assertFalse(t.travado)

    def test_failed_lock_unlocks(self):
        link = Link(ok=False)
        t = doa.TravaFeixe(link, log=lambda m: None, relogio=lambda: 100.0)
        self.assertFalse(t.travar(rad(30)))
        self.assertFalse(t.travado)
        self.assertEqual(link.escritas[-2:], [("AEC_FIXEDBEAMSONOFF", [0]), ("AUDIO_MGR_OP_R", [7, 3])])

    def test_ceiling_unlocks(self):
        agora = {"t": 100.0}
        link = Link()
        t = doa.TravaFeixe(link, log=lambda m: None, teto_s=90, relogio=lambda: agora["t"])
        t.travar(rad(30))
        agora["t"] = 189.0; t.tique(); self.assertTrue(t.travado)
        agora["t"] = 191.0; t.tique(); self.assertFalse(t.travado)

    def test_unlock_when_not_locked_writes_nothing(self):
        link = Link()
        t = doa.TravaFeixe(link, log=lambda m: None, relogio=lambda: 1.0)
        self.assertTrue(t.soltar())
        self.assertEqual(link.escritas, [])


class TestRegra(unittest.TestCase):
    """Regra do ESPHome (dono, 17/11): trava na palavra, solta quando acaba o primeiro pedido."""
    def setUp(self):
        self.link = Link()
        self.d = doa.Direcao()
        for t in (9.0, 9.2, 9.4):
            self.d.alimentar(t, rad(45))
        self.trava = doa.TravaFeixe(self.link, log=lambda m: None, relogio=lambda: 10.0)

    def test_no_lock_without_decision(self):
        r = doa.Regra(doa.Direcao(), self.trava, ligada=lambda: True)
        self.assertIsNone(r.palavra(10.0))
        self.assertEqual(self.link.escritas, [])

    def test_switch_off_never_locks(self):
        r = doa.Regra(self.d, self.trava, ligada=lambda: False)
        self.assertIsNone(r.palavra(10.0))
        self.assertEqual(self.link.escritas, [])

    def test_releases_when_first_request_ends(self):
        r = doa.Regra(self.d, self.trava, ligada=lambda: True)
        self.assertAlmostEqual(r.palavra(10.0), 45, delta=0.5)  # graus para o anel
        r.fala_parou()  # o fim da própria palavra, antes do pedido começar: não solta
        self.assertTrue(self.trava.travado)
        r.fala_comecou(); r.fala_parou()
        self.assertFalse(self.trava.travado)

    def test_conversation_close_releases(self):
        r = doa.Regra(self.d, self.trava, ligada=lambda: True)
        r.palavra(10.0)
        r.conversa_fechou()
        self.assertFalse(self.trava.travado)


if __name__ == "__main__":
    unittest.main()
