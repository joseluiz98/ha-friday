import datetime as dt
import unittest

import report as r


class TestReport(unittest.TestCase):
    def test_file_time(self):
        self.assertEqual(r.file_time("contexto_20261021_202454.wav"), dt.datetime(2026, 10, 21, 20, 24, 54))

    def test_window_max_cobre_atraso_do_modelo(self):
        s = [0.0] * 100
        s[40] = 0.9  # 3,2 s: 1,0 s depois do fim da palavra (2,2 s)
        self.assertEqual(r.window_max(s, 2.0, 2.2), 0.9)
        self.assertEqual(r.window_max(s, 0.0, 0.3), 0.0)

    def test_wake_words_tempo_absoluto_e_ativadora(self):
        t = [{"arq": "contexto_20261021_202454.wav", "palavras": [
            {"w": " Friday.", "i": 10.0, "f": 10.4, "p": 0.9}, {"w": " casa", "i": 12.0, "f": 12.3, "p": 0.9},
            {"w": " Friday", "i": 29.0, "f": 29.4, "p": 0.9}]}]
        ws = r.wake_words(t, {"contexto_20261021_202454.wav": [0.0] * 375})
        self.assertEqual(len(ws), 2)
        self.assertEqual(ws[0]["t"], dt.datetime(2026, 10, 21, 20, 24, 34))
        self.assertFalse(ws[0]["ativadora"])
        self.assertTrue(ws[1]["ativadora"])

    def test_dedupe_contextos_sobrepostos(self):
        t0 = dt.datetime(2026, 10, 20, 19, 11, 0)
        ws = [{"t": t0, "ativadora": False}, {"t": t0 + dt.timedelta(seconds=0.2), "ativadora": True},
              {"t": t0 + dt.timedelta(seconds=2), "ativadora": False}]
        out = r.dedupe(ws)
        self.assertEqual(len(out), 2)
        self.assertTrue(out[0]["ativadora"])

    def test_classify(self):
        t0 = dt.datetime(2026, 10, 20, 22, 58, 0)
        w = {"t": t0, "ativadora": False}
        ev = lambda tipo, s, voz=0.9: {"tipo": tipo, "t": t0 + dt.timedelta(seconds=s), "voz": voz}
        self.assertEqual(r.classify(w, [ev("barrada_verificador", 1.0)]), "barrada")
        self.assertEqual(r.classify(w, [ev("barrada_verificador", 1.0, voz=0.05)]), "nada")
        self.assertEqual(r.classify(w, [ev("quase", 2.0)]), "quase")
        self.assertEqual(r.classify(w, [ev("ativou", 5.0)]), "nada")
        self.assertEqual(r.classify(dict(w, ativadora=True), []), "ativou")


if __name__ == "__main__":
    unittest.main()
