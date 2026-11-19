import unittest

import ablation_report as ar


class TestAmbientSets(unittest.TestCase):
    def test_legacy_and_per_satellite_names(self):
        sc = {"amb20261103": {}, "amb20261104_sat-0a1b2c": {}, "sessoes": {}, "nat_ambiente": {}, "ambx": {}}
        self.assertEqual(ar.ambient_sets(sc), ["amb20261103", "amb20261104_sat-0a1b2c"])

    def test_same_file_name_in_two_satellites_is_kept_apart(self):
        f = "ambiente_20261119_101500.wav"
        sc = {"amb20261119_sat-0a1b2c": {f: {"m": [[1, .9, .9]]}}, "amb20261119_sat-1d2e3f": {f: {"m": []}}}
        blocos = ar.ambient_blocks(sc)
        self.assertEqual(len(blocos), 2)
        self.assertEqual({ar.ts(k).isoformat() for k in blocos}, {"2026-11-19T10:15:00"})


if __name__ == "__main__":
    unittest.main()
