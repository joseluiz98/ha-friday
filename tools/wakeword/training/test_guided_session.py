"""O compasso da sessão guiada (música/TV) precisa casar com o recorte do harvest: python3 -m unittest -q test_guided_session"""
import importlib.util, os, unittest

import dataset_rules as dr

_spec = importlib.util.spec_from_file_location("pms", os.path.join(os.path.dirname(__file__), "pi_guided_session.py"))
pms = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pms)


class TestSchedule(unittest.TestCase):
    def test_ticks_match_harvest_windows(self):
        ticks = pms.schedule(20)
        total = ticks[-1] + pms.INTERVALO + 1
        janelas = dr.recortes_tentativas(total)
        self.assertEqual(len(janelas), 20)
        for (j0, _), t in zip(janelas, ticks):
            self.assertAlmostEqual(j0, t + 0.2, places=2)


if __name__ == "__main__":
    unittest.main()
