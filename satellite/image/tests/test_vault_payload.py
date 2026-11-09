"""Image payload from the vault (plan 2026-11-09 Task 7): names map to payload paths; any vault error aborts."""
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import vault_payload as vp  # noqa: E402


class TestPayloadPath(unittest.TestCase):
    def test_mapping(self):
        self.assertEqual(vp.payload_path("satellite/program/vendor/xvf_host.py"), "programa/vendor/xvf_host.py")
        self.assertEqual(vp.payload_path("satellite/sounds/fim_a.wav"), "programa/sons/fim_a.wav")
        self.assertEqual(vp.payload_path("satellite/sounds/atuais-20261012/fim_a.wav"), "programa/sons/fim_a.wav")
        self.assertEqual(vp.payload_path("satellite/sounds/onboarding/ready.wav"), "programa/sons/onboarding/ready.wav")
        self.assertEqual(vp.payload_path("satellite/models/friday.onnx"), "modelos/friday.onnx")
        self.assertEqual(vp.payload_path("satellite/image/ha_authorized_key.pub"), "ha_authorized_key.pub")
        self.assertEqual(vp.payload_path("satellite/sendspin/sendspin-cli.tar.gz"), "sendspin/sendspin-cli.tar.gz")
        self.assertIsNone(vp.payload_path("other/thing"))


class TestFill(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.root, self.man, self.out = (os.path.join(self.d, x) for x in ("vault", "manifest.json", "payload"))
        src = os.path.join(self.d, "src"); os.makedirs(src)
        for name, data in (("top.wav", b"top"), ("old.wav", b"old"), ("snap_top.wav", b"snap")):
            with open(os.path.join(src, name), "wb") as f:
                f.write(data)
        vp.vault.put(os.path.join(src, "top.wav"), "satellite/sounds/fim_a.wav", root=self.root, manifest=self.man)
        vp.vault.put(os.path.join(src, "snap_top.wav"), "satellite/sounds/atuais-20261012/fim_a.wav", root=self.root, manifest=self.man)
        vp.vault.put(os.path.join(src, "old.wav"), "satellite/sounds/atuais-20261012/so_antigo.wav", root=self.root, manifest=self.man)

    def test_top_level_sound_wins_over_snapshot(self):
        vp.fill(self.out, root=self.root, manifest=self.man, required=())
        with open(os.path.join(self.out, "programa/sons/fim_a.wav"), "rb") as f:
            self.assertEqual(f.read(), b"top")
        self.assertTrue(os.path.exists(os.path.join(self.out, "programa/sons/so_antigo.wav")))

    def test_vault_error_aborts_and_writes_nothing(self):
        m = json.load(open(self.man))
        m["files"]["satellite/models/friday.onnx"] = {"sha256": "0" * 64, "size": 1, "source": "x"}
        json.dump(m, open(self.man, "w"))
        with self.assertRaises(vp.vault.VaultError):
            vp.fill(self.out, root=self.root, manifest=self.man)
        self.assertFalse(os.path.exists(os.path.join(self.out, "programa")))


if __name__ == "__main__":
    unittest.main()


class TestRequired(unittest.TestCase):
    def test_missing_required_set_aborts_before_writing(self):
        d = tempfile.mkdtemp()
        root, man, out = (os.path.join(d, x) for x in ("vault", "manifest.json", "payload"))
        src = os.path.join(d, "a.wav")
        with open(src, "wb") as f:
            f.write(b"a")
        vp.vault.put(src, "satellite/sounds/a.wav", root=root, manifest=man)
        with self.assertRaises(vp.vault.VaultError) as cm:
            vp.fill(out, root=root, manifest=man)
        self.assertIn("satellite/models/", str(cm.exception))
        self.assertFalse(os.path.exists(out))

    def test_default_required_sets(self):
        self.assertEqual(set(vp.REQUIRED), {"satellite/program/vendor/", "satellite/sounds/", "satellite/sounds/onboarding/",
                                            "satellite/models/", "satellite/sendspin/", "satellite/image/ha_authorized_key.pub"})
