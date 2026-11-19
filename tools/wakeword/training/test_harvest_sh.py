"""harvest.sh and analysis/run.sh: satellite and Pi address resolution (18/11/2026), with fake ssh/scp on PATH.
cd tools/wakeword/training && TMPDIR=<private dir> python3 -m unittest -q test_harvest_sh"""
import json, os, subprocess, tempfile, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
OFFICE, OTHER = "sat-0a1b2c", "sat-1d2e3f"
CLIP = "/srv/fake-pi/gravacoes/ativacoes/20261015_180746.wav"

FAKE_SSH = r"""#!/bin/bash
printf '%s\n' "$*" >> "$FAKE_LOG"
case "$*" in
  *"sudo sed -n"*) [ -n "${FAKE_VM_OFFICE:-}" ] && echo "$FAKE_VM_OFFICE" ;;  # the VM's sed prints the value
  *"python3 - "*) cat > /dev/null; printf 'ativou\t%s\tabc\n' "$FAKE_CLIP" ;;
  *"ls contexto_"*) ;;
esac
exit 0
"""

FAKE_SCP = r"""#!/bin/bash
printf 'scp %s\n' "$*" >> "$FAKE_LOG"
dst=${@: -1}
case "$dst" in *:*) ;; *) [ -d "$dst" ] || : > "$dst" ;; esac
exit 0
"""


def _write(path, text):
    with open(path, "w") as f:
        f.write(text)


def _read(path):
    with open(path) as f:
        return f.read()


class ShellCase(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(dir=os.environ.get("TMPDIR"))
        b = os.path.join(self.root, "bin")
        os.makedirs(b)
        for name, body in (("ssh", FAKE_SSH), ("scp", FAKE_SCP)):
            _write(os.path.join(b, name), body)
            os.chmod(os.path.join(b, name), 0o755)
        self.dot = os.path.join(self.root, "dot")
        os.makedirs(self.dot)
        _write(os.path.join(self.dot, "vm"), "vm@192.0.2.20\n")
        _write(os.path.join(self.dot, "mk1"), "192.0.2.5\n")
        self.sats = os.path.join(self.root, "satellites.json")
        _write(self.sats, json.dumps({OTHER: {"ip": "192.0.2.7", "comodo": "x"}}))
        self.log = os.path.join(self.root, "ssh.log")
        self.env = {"PATH": b + ":" + os.environ["PATH"], "FAKE_LOG": self.log, "FAKE_CLIP": CLIP,
                    "FRIDAY_DOT_DIR": self.dot, "FRIDAY_SATELLITES_JSON": self.sats,
                    "FRIDAY_LOCAL_CONFIG": os.path.join(self.root, "none.env"),
                    "FRIDAY_WAKEWORD_DATA": os.path.join(self.root, "wakeword-data"),
                    "FRIDAY_OWNER_VOICE_ID": "voice-0000", "FRIDAY_SAT_OFFICE": OFFICE, "TMPDIR": self.root}

    def log_lines(self):
        return _read(self.log).splitlines() if os.path.exists(self.log) else []

    def pi_calls(self, host):
        return [l for l in self.log_lines() if l.startswith(f"jose@{host} ") or f"jose@{host}:" in l]


class TestHarvestSh(ShellCase):
    def harvest(self, *args, **env):
        self.work = os.path.join(self.root, "work")
        e = dict(self.env, **env)
        return subprocess.run(["bash", os.path.join(HERE, "harvest.sh"), "friday", self.work, *args],
                              env=e, capture_output=True, text=True, timeout=60)

    def sources(self):
        return [json.loads(l) for l in _read(os.path.join(self.work, "sources.ndjson")).splitlines()]

    def test_sat_from_satellites_json_and_prefix(self):
        r = self.harvest("--sat", OTHER)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(self.pi_calls("192.0.2.7"))
        self.assertFalse(self.pi_calls("192.0.2.5"))
        s = self.sources()
        self.assertEqual([(x["name"], x["sat"]) for x in s], [(OTHER + "__ativou__20261015_180746.wav", OTHER)])
        nomes = _read(os.path.join(self.work, "nomes.txt"))
        self.assertIn(OTHER + "__ativou__20261015_180746.wav " + CLIP, nomes)

    def test_default_is_office_on_mk1(self):
        r = self.harvest()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(self.pi_calls("192.0.2.5"))
        self.assertEqual(self.sources()[0]["name"], OFFICE + "__ativou__20261015_180746.wav")

    def test_host_wins(self):
        r = self.harvest("--sat", OTHER, "--host", "192.0.2.9")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(self.pi_calls("192.0.2.9"))
        self.assertFalse(self.pi_calls("192.0.2.7"))
        self.assertEqual(self.sources()[0]["sat"], OTHER)

    def test_office_sat_missing_from_json_falls_back_to_mk1(self):
        r = self.harvest("--sat", OFFICE)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(self.pi_calls("192.0.2.5"))

    def test_unknown_sat_refuses_before_any_pi_call(self):
        r = self.harvest("--sat", "sat-999999")
        self.assertEqual(r.returncode, 2)
        self.assertIn("--host", r.stderr)
        self.assertFalse([l for l in self.log_lines() if l.startswith("jose@")])

    def test_invalid_sat_refuses(self):
        for bad in ("../x", "sat-XYZ", "sat-0a1b2c;rm"):
            r = self.harvest("--sat", bad)
            self.assertEqual(r.returncode, 2, bad)

    def test_office_from_the_vm(self):
        env = dict(self.env)
        del env["FRIDAY_SAT_OFFICE"]
        self.env = env
        r = self.harvest(FAKE_VM_OFFICE=OFFICE)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.sources()[0]["sat"], OFFICE)

    def test_office_from_local_config(self):
        envf = os.path.join(self.root, "local.env")
        _write(envf, f"FRIDAY_SAT_OFFICE={OTHER}\n")
        del self.env["FRIDAY_SAT_OFFICE"]
        r = self.harvest(FRIDAY_LOCAL_CONFIG=envf)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.sources()[0]["sat"], OTHER)
        self.assertTrue(self.pi_calls("192.0.2.5"))

    def test_no_office_anywhere_refuses(self):
        del self.env["FRIDAY_SAT_OFFICE"]
        r = self.harvest()
        self.assertEqual(r.returncode, 2)
        self.assertIn("FRIDAY_SAT_OFFICE", r.stderr)

    def test_rulings_go_to_their_satellite(self):
        d = os.path.join(self.root, "wakeword-data", "friday")
        os.makedirs(d)
        _write(os.path.join(d, "rulings.json"),
               json.dumps({"falsas_ativacoes": ["20261030_143729.wav", OTHER + "__20261031_090000.wav"]}))
        r = self.harvest("--sat", OTHER)
        self.assertEqual(r.returncode, 0, r.stderr)
        lista = [l for l in self.pi_calls("192.0.2.7") if "python3 - " in l]
        self.assertEqual(lista, ["jose@192.0.2.7 python3 - friday 20261031_090000.wav"])


class TestAnalysisRunSh(ShellCase):
    def run_sh(self, *args):
        out = os.path.join(self.root, "out")
        r = subprocess.run(["bash", os.path.join(HERE, "..", "analysis", "run.sh"), "--since", "20261101",
                            "--out", out, *args], env=self.env, capture_output=True, text=True, timeout=60)
        return r, out

    def test_sat_resolves_its_pi(self):
        r, out = self.run_sh("--sat", OTHER)
        self.assertEqual(r.returncode, 1)  # the fake Pi has no contexto_*.wav
        self.assertIn("nenhum contexto", r.stdout)
        self.assertTrue(self.pi_calls("192.0.2.7"))
        self.assertEqual(_read(os.path.join(out, "sat")).strip(), OTHER)

    def test_default_is_mk1_and_host_wins(self):
        self.run_sh()
        self.assertTrue(self.pi_calls("192.0.2.5"))
        self.run_sh("--sat", OTHER, "--host", "192.0.2.9")
        self.assertTrue(self.pi_calls("192.0.2.9"))
        self.assertFalse(self.pi_calls("192.0.2.7"))

    def test_unknown_sat_refuses(self):
        r, _ = self.run_sh("--sat", "sat-999999")
        self.assertEqual(r.returncode, 2)


if __name__ == "__main__":
    unittest.main()
