"""cd /homeassistant/tools/satelite/programa && python3 -m unittest test_ducking -v"""
import unittest
from ducking import MusicDucker, should_duck


class Fake:
    def __init__(self):
        self.escritas = []

    def __call__(self, p):
        self.escritas.append(p)


def tiques(d, ativo, ini, fim, passo=0.1):
    t = ini
    while t <= fim + 1e-9:
        d.update(ativo, t)
        t += passo


class TestDucker(unittest.TestCase):
    def setUp(self):
        self.w = Fake()
        self.d = MusicDucker(self.w)

    def test_start_restores_full_volume(self):
        self.d.start(0)
        self.assertEqual(self.w.escritas[-1], 100)

    def test_ducks_down_with_ramp(self):
        self.d.start(0)
        n0 = len(self.w.escritas)
        for t in (1.0, 1.15, 1.3):
            self.d.update(True, t)
        novas = self.w.escritas[n0:]
        self.assertEqual(novas[-1], 15)
        self.assertEqual(novas, sorted(novas, reverse=True))
        self.assertTrue(all(p >= 15 for p in novas))
        self.assertGreater(len(novas), 1)  # rampa, não salto

    def test_stays_ducked_while_active(self):
        self.d.start(0)
        tiques(self.d, True, 1.0, 1.5)
        n = len(self.w.escritas)
        tiques(self.d, True, 1.6, 10.0)
        self.assertEqual(len(self.w.escritas), n)

    def test_restores_after_restore_s(self):
        self.d.start(0)
        tiques(self.d, True, 1.0, 5.0)
        self.d.update(False, 5.5)
        self.assertEqual(self.w.escritas[-1], 15)
        tiques(self.d, False, 5.6, 5.9)
        self.assertEqual(self.w.escritas[-1], 15)  # ainda dentro de restore_s (1,0 s)
        tiques(self.d, False, 6.0, 6.35, 0.05)
        self.assertEqual(self.w.escritas[-1], 100)

    def test_reactivation_cancels_restore(self):
        self.d.start(0)
        tiques(self.d, True, 1.0, 5.0)
        n = len(self.w.escritas)
        self.d.update(False, 5.0)
        tiques(self.d, False, 5.1, 5.5)
        self.d.update(True, 5.6)
        tiques(self.d, True, 5.7, 8.0)
        self.assertEqual(self.w.escritas[n:], [])

    def test_writes_only_on_change(self):
        self.d.start(0)
        tiques(self.d, False, 0.1, 5.0)
        self.assertEqual(len(self.w.escritas), 1)

    def test_writer_error_does_not_raise(self):
        logs = []

        def ruim(p):
            if p != 100:
                raise RuntimeError("amixer")
        d = MusicDucker(ruim, log=logs.append)
        d.start(0)
        d.update(True, 1.0)
        d.update(True, 1.3)
        self.assertTrue(any("music_duck_error" in str(l) for l in logs))

    def test_should_duck(self):
        self.assertTrue(should_duck("ouvindo", None, 10.0))
        self.assertTrue(should_duck("alarme", None, 10.0))
        self.assertTrue(should_duck("parada", 9.8, 10.0))
        self.assertFalse(should_duck("parada", None, 10.0))
        self.assertFalse(should_duck("parada", 9.0, 10.0))

    def test_percent_hot_change(self):
        self.d.start(0)
        self.d.duck_percent = 5
        tiques(self.d, True, 1.0, 2.0)
        self.assertEqual(self.w.escritas[-1], 5)


class TestRampaMusica(unittest.TestCase):
    """Alarme crescente com música: o `Music` sobe de 10% a 100% em alarm_ramp_s (spec §11)."""
    def test_ramp_to_rises_monotonically_to_full(self):
        w = Fake()
        d = MusicDucker(w)
        d.start(0)
        d.ramp_to(10, 30, 100.0)
        tiques(d, False, 100.0, 131.0, 0.5)
        sobe = w.escritas[1:]
        self.assertEqual(sobe, sorted(sobe))
        self.assertEqual(sobe[0], 10)
        self.assertEqual(sobe[-1], 100)

    def test_ramp_ignores_residual_output_ducking_at_start(self):
        w = Fake()
        d = MusicDucker(w)
        d.start(0)
        d.ramp_to(10, 20, 100.0)
        tiques(d, True, 100.0, 100.5, 0.05)  # o fim da campainha ainda conta como saída do programa
        tiques(d, False, 100.55, 110.0, 0.05)
        self.assertTrue(40 <= w.escritas[-1] <= 70, w.escritas[-1])  # meio da subida de 20 s, não restaurado em 1 s
        tiques(d, False, 110.05, 121.0, 0.05)
        depois = w.escritas[1:]
        self.assertEqual(depois, sorted(depois))
        self.assertEqual(w.escritas[-1], 100)

    def test_speech_during_ramp_ducks_normally(self):
        w = Fake()
        d = MusicDucker(w)
        d.start(0)
        d.ramp_to(10, 60, 100.0)
        tiques(d, False, 100.0, 110.0, 0.1)
        tiques(d, True, 110.1, 112.0, 0.1)  # alguém fala bem depois do início
        self.assertEqual(w.escritas[-1], 15)


if __name__ == "__main__":
    unittest.main()


class TestSendspinStatus(unittest.TestCase):
    def test_sendspin_playing(self):
        from ducking import sendspin_playing
        self.assertIs(sendspin_playing("name: X\nstate: playing\nstream: active\n"), True)
        self.assertIs(sendspin_playing("  state: playing  \n"), True)
        self.assertIs(sendspin_playing("state: paused"), False)
        self.assertIs(sendspin_playing(""), False)
        self.assertIs(sendspin_playing("state: playing-ish"), False)
