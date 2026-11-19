"""build_dataset with satellite-prefixed clips (18/11/2026). Needs numpy: run on the VM with
FRIDAY_TEST_DIR=<dir> scripts/test.sh tools/wakeword/training/test_build_dataset_sat.py -q"""
import io, json, os, tempfile, unittest, wave
from contextlib import redirect_stderr, redirect_stdout

try:
    import build_dataset as bd
except ImportError:  # numpy missing (HA box)
    bd = None

OFFICE, OTHER = "sat-0a1b2c", "sat-1d2e3f"


def _wav(path, secs=3.0):
    with wave.open(path, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
        w.writeframes(b"\x00\x00" * int(secs * 16000))


@unittest.skipIf(bd is None, "numpy not available")
class TestBuildDatasetSat(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(dir=os.environ.get("TMPDIR"))
        self.work = os.path.join(self.root, "work")
        os.makedirs(os.path.join(self.work, "wav"))
        names = [(OFFICE, "acervo_v__20261015_180746.wav", "2026-10-15T18:07:46"),
                 (OTHER, "acervo_v__20261015_180746.wav", "2026-11-15T18:07:46")]
        with open(os.path.join(self.work, "sources.ndjson"), "w") as s, \
                open(os.path.join(self.work, "transcricoes.ndjson"), "w") as t, \
                open(os.path.join(self.work, "scores.ndjson"), "w") as n:
            for sat, base, ts in names:
                name = f"{sat}__{base}"
                _wav(os.path.join(self.work, "wav", name))
                s.write(json.dumps({"src": "acervo_v", "path": "/p/" + base, "name": name, "ts": ts, "sha1": sat,
                                    "sat": sat}) + "\n")
                t.write(json.dumps({"arq": name, "palavras": [{"w": " Friday", "i": 1.0, "f": 1.4, "p": 0.9}]}) + "\n")
                n.write(json.dumps({"name": name, "dur": 3.0, "base": [0.0] * 37, "verif": [0.0] * 37}) + "\n")
        json.dump([], open(os.path.join(self.work, "marcas.json"), "w"))
        self.params = os.path.join(self.root, "params")
        os.makedirs(self.params)
        json.dump({"model_name": "friday"}, open(os.path.join(self.params, "friday.json"), "w"))
        # previous cycle (before satellite ids): the office clip was holdout under its legacy name
        self.old = os.path.join(self.root, "old.ndjson")
        with open(self.old, "w") as f:
            f.write(json.dumps({"id": "acervo_v__20261015_180746.wav@1.00", "name": "acervo_v__20261015_180746.wav",
                                "split": "holdout"}) + "\n")

    def _build(self, *extra):
        out = os.path.join(self.root, "out")
        o, e = io.StringIO(), io.StringIO()
        with redirect_stdout(o), redirect_stderr(e):
            rc = bd.main(["friday", self.work, out, "--seed", "1", "--params-dir", self.params, *extra])
        man = [json.loads(l) for l in open(os.path.join(out, "manifest.ndjson"))] if rc == 0 else []
        return rc, man, e.getvalue()

    def test_manifest_has_sat_and_legacy_holdout_stays(self):
        rc, man, err = self._build("--pin-from", self.old, "--office", OFFICE)
        self.assertEqual(rc, 0, err)
        by = {(i["sat"], i["split"]) for i in man}
        self.assertIn((OFFICE, "holdout"), by)
        self.assertEqual({i["sat"] for i in man}, {OFFICE, OTHER})
        office = [i for i in man if i["sat"] == OFFICE]
        self.assertTrue(office and all(i["split"] == "holdout" for i in office))

    def test_legacy_pin_without_office_refuses(self):
        env = {k: os.environ.pop(k) for k in ("FRIDAY_SAT_OFFICE",) if k in os.environ}
        old_cfg = os.environ.get("FRIDAY_LOCAL_CONFIG")
        os.environ["FRIDAY_LOCAL_CONFIG"] = os.path.join(self.root, "missing.env")
        try:
            rc, _, err = self._build("--pin-from", self.old)
        finally:
            os.environ.update(env)
            if old_cfg is None:
                os.environ.pop("FRIDAY_LOCAL_CONFIG", None)
            else:
                os.environ["FRIDAY_LOCAL_CONFIG"] = old_cfg
        self.assertEqual(rc, 2)
        self.assertIn("FRIDAY_SAT_OFFICE", err)


if __name__ == "__main__":
    unittest.main()
