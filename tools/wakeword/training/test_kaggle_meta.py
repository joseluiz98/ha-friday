"""cd /homeassistant/tools/satelite/wakeword-training && python3 -m unittest -q test_kaggle_meta"""
import json, os, subprocess, sys, tempfile, unittest
import kaggle_meta as km


class TestKaggleMeta(unittest.TestCase):
    def test_kernel_metadata_privado_com_gpu_e_internet(self):
        m = km.kernel_metadata("friday")
        self.assertIs(m["is_private"], True)
        self.assertIs(m["enable_gpu"], True)
        self.assertIs(m["enable_internet"], True)
        self.assertEqual(m["dataset_sources"], ["joseluiz98/wakeword-friday-data"])
        self.assertEqual(m["id"], "joseluiz98/wakeword-friday")
        self.assertEqual(m["code_file"], "wakeword_v2.ipynb")

    def test_slug_troca_underscore(self):
        self.assertEqual(km.kernel_metadata("sexta_feira")["id"], "joseluiz98/wakeword-sexta-feira")
        self.assertEqual(km.slug("sexta_feira"), "sexta-feira")

    def test_dataset_metadata_privado_por_padrao(self):
        m = km.dataset_metadata("sexta_feira")
        self.assertEqual(m["id"], "joseluiz98/wakeword-sexta-feira-data")
        self.assertNotIn("isPrivate", m)  # privado = o padrão do `datasets create` sem --public

    def test_decide(self):
        for t in ("complete", "KernelWorkerStatus.COMPLETE", "COMPLETE"):
            self.assertEqual(km.decide(t), "complete", t)
        for t in ("running", "queued", "KernelWorkerStatus.RUNNING"):
            self.assertEqual(km.decide(t), "running", t)
        for t in ("error", "cancelAcknowledged", "KernelWorkerStatus.ERROR", ""):
            self.assertEqual(km.decide(t), "failed", t)

    def test_cli_meta_escreve_arquivos(self):
        with tempfile.TemporaryDirectory() as d:
            subprocess.run([sys.executable, "-I", os.path.join(os.path.dirname(__file__), "kaggle_meta.py"),
                            "meta", "friday", d], check=True)
            self.assertEqual(json.load(open(f"{d}/kernel-metadata.json"))["id"], "joseluiz98/wakeword-friday")
            self.assertEqual(json.load(open(f"{d}/data/dataset-metadata.json"))["id"],
                             "joseluiz98/wakeword-friday-data")

    def test_cli_decide(self):
        r = subprocess.run([sys.executable, "-I", os.path.join(os.path.dirname(__file__), "kaggle_meta.py"),
                            "decide", "KernelWorkerStatus.RUNNING"], capture_output=True, text=True, check=True)
        self.assertEqual(r.stdout.strip(), "running")


if __name__ == "__main__":
    unittest.main()
