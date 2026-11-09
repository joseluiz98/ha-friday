"""cd /config/tools/satelite/imagem && python3 -m unittest -q tests.test_payload_check"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import payload_check as pc  # noqa: E402


def put(root, rel, data=b"x"):
    p = os.path.join(root, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "wb") as f:
        f.write(data)


class T(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.reg = os.path.join(self.d, "satellites.json")
        json.dump({"sat-1": {"senha": "SUPER-SECRET-VALUE", "ip": "1.2.3.4"}}, open(self.reg, "w"))
        self.p = os.path.join(self.d, "payload")

    def test_clean_payload(self):
        put(self.p, "programa/friday_satelite.py", b"print('hi')")
        self.assertEqual(pc.scan(self.p, pc.secret_values(self.reg)), [])

    def test_secret_value_is_found_without_being_printed(self):
        put(self.p, "programa/x.py", b"T = 'SUPER-SECRET-VALUE'")
        out = pc.scan(self.p, pc.secret_values(self.reg))
        self.assertEqual(len(out), 1)
        self.assertNotIn("SUPER-SECRET-VALUE", out[0])

    def test_forbidden_names_and_production_id(self):
        put(self.p, "modelos/friday_verificador.pkl")
        put(self.p, "home/.ssh/id_satsync")
        put(self.p, "x/vozes/ana/a.wav")
        put(self.p, "programa/c.py", b"# sat-0a1b2c")
        saved, pc.PRODUCTION_IDS = pc.PRODUCTION_IDS, ("sat-0a1b2c",)  # the real ids come from the private list
        try:
            out = "\n".join(pc.scan(self.p, []))
        finally:
            pc.PRODUCTION_IDS = saved
        for expected in ("friday_verificador.pkl", "id_satsync", "vozes", "production satellite id"):
            self.assertIn(expected, out)

    def test_program_modules_named_after_voices_are_code_not_data(self):
        for name in ("preparar_vozes.py", "verificador.py", "treino_verificador.py", "cadastro.py"):
            put(self.p, "programa/" + name, b"# code")
        put(self.p, "files/etc/tmpfiles-friday-gravacoes.conf")
        self.assertEqual(pc.scan(self.p, []), [])

    def test_short_values_are_not_secrets_and_bad_registry_is_ignored(self):
        json.dump({"a": {"senha": "abc"}}, open(self.reg, "w"))
        self.assertEqual(pc.secret_values(self.reg), [])
        self.assertEqual(pc.secret_values(os.path.join(self.d, "missing.json")), [])

    def test_cli_exit_codes(self):
        put(self.p, "ok.txt", b"fine")
        self.assertEqual(pc.main([self.p]), 0)
        put(self.p, "bad/vozes/a.wav")
        self.assertEqual(pc.main([self.p]), 1)


if __name__ == "__main__":
    unittest.main()


class TestProductionIds(unittest.TestCase):
    def test_reads_satellite_ids_from_private_list(self):
        d = tempfile.mkdtemp(); f = os.path.join(d, "s.txt")
        with open(f, "w") as fh:
            fh.write("# c\nperson_name|Zelda\ndevice_id|sat-0a1b2c\ndevice_id|ABC123\n")
        self.assertEqual(pc.production_ids(f), ("sat-0a1b2c",))

    def test_missing_list_gives_no_ids(self):
        self.assertEqual(pc.production_ids("/nonexistent/list"), ())
