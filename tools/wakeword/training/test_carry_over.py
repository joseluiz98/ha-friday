"""cd tools/wakeword/training && python3 -m unittest -q test_carry_over (18/11/2026: legacy and prefixed names)"""
import io, json, os, tempfile, unittest
from contextlib import redirect_stderr, redirect_stdout

import carry_over as co

OFFICE, OTHER = "sat-0a1b2c", "sat-1d2e3f"


def _cycle(root, name, items):
    d = os.path.join(root, name)
    for sub in ("upload/positive", "upload/negative"):
        os.makedirs(os.path.join(d, sub))
    with open(os.path.join(d, "manifest.ndjson"), "w") as f:
        for it in items:
            f.write(json.dumps(it) + "\n")
    return d


def _it(name, split, ini="1.00", label="pos"):
    return {"id": f"{name}@{ini}", "name": name, "src": "ativou", "label": label, "split": split}


class TestCarryOver(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(dir=os.environ.get("TMPDIR"))

    def _run(self, new, olds, office=OFFICE):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = co.main(new, olds, office)
        return rc, out.getvalue(), err.getvalue()

    def test_legacy_holdout_prefixed_train_leaks(self):
        old = _cycle(self.root, "c1", [_it("20261015_180746.wav", "holdout")])
        new = _cycle(self.root, "c2", [_it(OFFICE + "__20261015_180746.wav", "train")])
        rc, out, _ = self._run(new, [old])
        self.assertEqual(rc, 3)
        self.assertIn(OFFICE + "__20261015_180746.wav@1.00", out)

    def test_legacy_file_all_holdout_prefixed_other_window_leaks(self):
        old = _cycle(self.root, "c1", [_it("20261015_180746.wav", "holdout")])
        new = _cycle(self.root, "c2", [_it(OFFICE + "__20261015_180746.wav", "train", ini="4.00")])
        self.assertEqual(self._run(new, [old])[0], 3)

    def test_other_satellite_same_base_name_does_not_leak(self):
        old = _cycle(self.root, "c1", [_it("20261015_180746.wav", "holdout")])
        new = _cycle(self.root, "c2", [_it(OTHER + "__20261015_180746.wav", "train")])
        self.assertEqual(self._run(new, [old])[0], 0)

    def test_source_still_harvested_under_prefix_is_not_carried(self):
        old = _cycle(self.root, "c1", [_it("20261015_180746.wav", "train")])
        open(os.path.join(old, "upload/positive/20261015_180746.wav_at_1.00.wav"), "w").close()
        new = _cycle(self.root, "c2", [_it(OFFICE + "__20261015_180746.wav", "train")])
        rc, out, _ = self._run(new, [old])
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out)["copiados_de_ciclos_anteriores"], 0)

    def test_legacy_names_without_office_refuse(self):
        old = _cycle(self.root, "c1", [_it("20261015_180746.wav", "holdout")])
        new = _cycle(self.root, "c2", [_it(OFFICE + "__20261015_180746.wav", "train")])
        rc, _, err = self._run(new, [old], office=None)
        self.assertEqual(rc, 2)
        self.assertIn("FRIDAY_SAT_OFFICE", err)


class TestOfficeLookup(unittest.TestCase):
    def test_env_then_local_config(self):
        d = tempfile.mkdtemp(dir=os.environ.get("TMPDIR"))
        envf = os.path.join(d, "local.env")
        with open(envf, "w") as f:
            f.write(f'FRIDAY_X=1\nFRIDAY_SAT_OFFICE="{OTHER}"\n')
        self.assertEqual(co.dr.office_id({"FRIDAY_LOCAL_CONFIG": envf}), OTHER)
        self.assertEqual(co.dr.office_id({"FRIDAY_LOCAL_CONFIG": envf, "FRIDAY_SAT_OFFICE": OFFICE}), OFFICE)
        self.assertIsNone(co.dr.office_id({"FRIDAY_LOCAL_CONFIG": os.path.join(d, "none.env")}))


if __name__ == "__main__":
    unittest.main()
