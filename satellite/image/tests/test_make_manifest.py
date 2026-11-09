"""cd /config/tools/satelite/imagem && python3 -m unittest -q tests.test_make_manifest"""
import hashlib
import json
import lzma
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
import make_manifest as mm  # noqa: E402

RAW = b"raw disk image bytes " * 5000


class TestManifest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.xz = os.path.join(self.d, "friday-satellite-20261105.img.xz")
        with lzma.open(self.xz, "wb") as f:
            f.write(RAW)

    def test_fields_and_hashes_from_the_real_files(self):
        entry = mm.build(self.xz, "http://vm/x.img.xz", "2026-11-05")["os_list"][0]
        self.assertEqual(entry["extract_size"], len(RAW))
        self.assertEqual(entry["extract_sha256"], hashlib.sha256(RAW).hexdigest())
        blob = open(self.xz, "rb").read()
        self.assertEqual(entry["image_download_size"], len(blob))
        self.assertEqual(entry["image_download_sha256"], hashlib.sha256(blob).hexdigest())

    def test_imager_metadata(self):
        entry = mm.build(self.xz, "http://vm/x.img.xz", "2026-11-05")["os_list"][0]
        self.assertEqual(entry["init_format"], "cloudinit-rpi")
        self.assertEqual(entry["devices"], ["pi3-64bit"])
        self.assertEqual(entry["name"], "F.R.I.D.A.Y. Satellite 2026-11-05")
        self.assertEqual(entry["release_date"], "2026-11-05")
        for required in ("name", "description", "icon", "url", "extract_size", "extract_sha256", "image_download_size",
                         "release_date", "devices"):
            self.assertIn(required, entry)  # the Imager schema's required fields

    def test_known_extract_values_skip_decompression(self):
        entry = mm.build(self.xz, "http://vm/x", "2026-11-05", ("ab" * 32, 123))["os_list"][0]
        self.assertEqual((entry["extract_sha256"], entry["extract_size"]), ("ab" * 32, 123))

    def test_cli_writes_json_atomically(self):
        out = os.path.join(self.d, "m.json")
        r = subprocess.run([sys.executable, os.path.join(HERE, "make_manifest.py"), self.xz, "--url",
                            "http://192.0.2.20:8088/x.img.xz", "--out", out, "--date", "2026-11-05"],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.load(open(out))["os_list"][0]["url"], "http://192.0.2.20:8088/x.img.xz")
        self.assertFalse(os.path.exists(out + ".tmp"))

    def test_cli_rejects_bad_url_and_half_extract_args(self):
        base = [sys.executable, os.path.join(HERE, "make_manifest.py"), self.xz, "--out", os.path.join(self.d, "m.json")]
        self.assertNotEqual(subprocess.run(base + ["--url", "ftp://x"], capture_output=True).returncode, 0)
        self.assertNotEqual(subprocess.run(base + ["--url", "http://x", "--extract-size", "5"],
                                           capture_output=True).returncode, 0)


if __name__ == "__main__":
    unittest.main()
