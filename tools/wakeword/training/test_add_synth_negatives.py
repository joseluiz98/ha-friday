import json, os, tempfile, unittest, wave

import add_synth_negatives as a


class T(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        self.c = os.path.join(self.t.name, "ciclo"); self.g = os.path.join(self.t.name, "gen")
        os.makedirs(os.path.join(self.c, "upload", "negative")); os.makedirs(os.path.join(self.g, "aceitas"))
        ger = []
        for i in range(40):
            n = f"xttsneg__f{i}_0.wav"
            with wave.open(os.path.join(self.g, "aceitas", n), "wb") as w:
                w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000); w.writeframes(str(i).encode() * 50)
            ger.append({"falante": f"f{i}", "take": 0, "arquivo": n, "texto": "Festa"})
        ger.append({"falante": "x", "take": 0, "motivo": "transcrição"})
        ger.append({"falante": "f1", "take": 1, "arquivo": "xttsneg__f1_1.wav", "excluida": True})
        json.dump({"geradas": ger}, open(os.path.join(self.g, "manifesto.json"), "w"))

    def tearDown(self):
        self.t.cleanup()

    def test_prova_por_falante_e_sem_repetir(self):
        self.assertEqual(a.main([self.c, self.g, "--seed", "4"]), 0)
        tr = os.listdir(os.path.join(self.c, "upload", "negative"))
        ho = os.listdir(os.path.join(self.c, "holdout_native", "xttsneg"))
        self.assertEqual(len(tr) + len(ho), 40)
        self.assertEqual(len(ho), 2)
        self.assertFalse(set(tr) & set(ho))

    def test_mantem_outros_registros_e_e_idempotente(self):
        n = os.path.join(self.c, "native.ndjson")
        open(n, "w").write(json.dumps({"etq": "ambiente", "nome": "z"}) + "\n")
        a.main([self.c, self.g, "--seed", "4"]); a.main([self.c, self.g, "--seed", "4"])
        ls = [json.loads(l) for l in open(n)]
        self.assertEqual(sum(l["etq"] == "ambiente" for l in ls), 1)
        self.assertEqual(sum(l["etq"] == "xttsneg" for l in ls), 40)

    def test_arquivo_faltando_recusa(self):
        os.remove(os.path.join(self.g, "aceitas", "xttsneg__f3_0.wav"))
        self.assertEqual(a.main([self.c, self.g, "--seed", "4"]), 2)


if __name__ == "__main__":
    unittest.main()
