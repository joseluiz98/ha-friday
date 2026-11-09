import os
import tempfile
import unittest

import local_config as lc


class TestLocalConfig(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.file = os.path.join(self.dir, "local.env")
        self.env = {k: os.environ.pop(k) for k in ("FRIDAY_OWNER_NAME", "FRIDAY_X") if k in os.environ}

    def tearDown(self):
        os.environ.update(self.env)

    def test_reads_key_from_file(self):
        with open(self.file, "w") as f:
            f.write("# c\nFRIDAY_OWNER_NAME=Alex\n")
        self.assertEqual(lc.get("FRIDAY_OWNER_NAME", self.file), "Alex")

    def test_environment_wins(self):
        with open(self.file, "w") as f:
            f.write("FRIDAY_X=file\n")
        os.environ["FRIDAY_X"] = "env"
        try:
            self.assertEqual(lc.get("FRIDAY_X", self.file), "env")
        finally:
            del os.environ["FRIDAY_X"]

    def test_local_config_missing_key_names_it(self):
        with self.assertRaises(lc.MissingLocalConfig) as cm:
            lc.get("FRIDAY_OWNER_NAME", os.path.join(self.dir, "absent.env"))
        self.assertIn("FRIDAY_OWNER_NAME", str(cm.exception))

    def test_example_file_has_every_key_the_code_reads(self):
        here = os.path.dirname(os.path.abspath(__file__))
        keys = lc.load(os.path.join(here, "local.example.env"))
        for k in ("FRIDAY_OWNER_NAME", "FRIDAY_HA_URL", "FRIDAY_OWNER_NOTIFY", "FRIDAY_OWNER_PERSON", "FRIDAY_VM_IP"):
            self.assertIn(k, keys)


if __name__ == "__main__":
    unittest.main()
