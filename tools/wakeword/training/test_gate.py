"""cd /homeassistant/tools/satelite/wakeword-training && python3 -m unittest -q test_gate"""
import json, os, subprocess, sys, tempfile, unittest
import gate


def lado(recall, fp, total=100, detectados=None):
    return {"nome": "x.onnx", "pos_total": total, "pos_detectados": detectados if detectados is not None
            else round(recall * total), "recall": recall, "disparos_falsos": 0, "fp_por_hora": fp}


def rel(atual, cand, regress=0):
    return {"limiar": 0.6, "horas": 1.0, "atual": atual, "candidato": cand,
            "regressoes": [f"r{i}" for i in range(regress)], "ganhos": []}


class TestGate(unittest.TestCase):
    def test_reprova_recall_nao_melhorou(self):
        r = gate.gate(rel(lado(0.5, 1.0), lado(0.5, 1.0)), 0.1)
        self.assertEqual(r["verdict"], "reprovar"); self.assertIn("recall não melhorou", r["reasons"])
        r = gate.gate(rel(lado(0.5, 1.0), lado(0.4, 1.0)), 0.1)
        self.assertEqual(r["verdict"], "reprovar")

    def test_reprova_regressoes_acima_de_5_por_cento(self):
        # atual detecta 50; 3 regressões > 2,5
        r = gate.gate(rel(lado(0.5, 1.0), lado(0.8, 1.0), regress=3), 0.1)
        self.assertEqual(r["verdict"], "reprovar"); self.assertIn("regressões acima de 5%", r["reasons"])
        r = gate.gate(rel(lado(0.5, 1.0), lado(0.8, 1.0), regress=2), 0.1)  # 2 <= 2,5: passa
        self.assertNotEqual(r["verdict"], "reprovar")

    def test_reprova_mais_disparos_falsos(self):
        r = gate.gate(rel(lado(0.5, 1.0), lado(0.8, 1.01)), 0.1)
        self.assertEqual(r["verdict"], "reprovar"); self.assertIn("mais disparos falsos", r["reasons"])

    def test_fp_igual_nao_reprova(self):
        self.assertEqual(gate.gate(rel(lado(0.5, 1.0), lado(0.8, 1.0)), 0.1)["verdict"], "instalar")

    def test_sombra_por_ganho_pequeno(self):
        r = gate.gate(rel(lado(0.50, 1.0), lado(0.59, 0.5)), 0.1)
        self.assertEqual(r["verdict"], "sombra"); self.assertIn("ganho pequeno", r["reasons"])

    def test_sombra_por_fp_sintetico(self):
        r = gate.gate(rel(lado(0.5, 1.0), lado(0.8, 0.5)), 0.51)
        self.assertEqual(r["verdict"], "sombra"); self.assertIn("FP/h sintético alto", r["reasons"])

    def test_instalar(self):
        r = gate.gate(rel(lado(0.5, 1.0), lado(0.7, 0.5)), 0.5)
        self.assertEqual(r, {"verdict": "instalar", "reasons": []})

    def test_max_verdict_sombra_nunca_instala(self):
        r = gate.gate(rel(lado(0.5, 1.0), lado(0.9, 0.2)), 0.1, max_verdict="sombra")
        self.assertEqual(r["verdict"], "sombra")
        r = gate.gate(rel(lado(0.5, 1.0), lado(0.4, 0.2)), 0.1, max_verdict="sombra")
        self.assertEqual(r["verdict"], "reprovar")  # o teto nunca melhora um veredito

    def test_cli(self):
        with tempfile.TemporaryDirectory() as d:
            json.dump(rel(lado(0.5, 1.0), lado(0.9, 0.2)), open(f"{d}/e.json", "w"))
            json.dump({"val_fp_per_hour": 0.1}, open(f"{d}/m.json", "w"))
            out = subprocess.run([sys.executable, "-I", os.path.join(os.path.dirname(__file__), "gate.py"),
                                  f"{d}/e.json", f"{d}/m.json", "--max", "sombra"], capture_output=True, text=True,
                                 check=True).stdout
            self.assertEqual(json.loads(out)["verdict"], "sombra")


if __name__ == "__main__":
    unittest.main()
