import json, os, tempfile, unittest, wave

import add_ambient_positives as a


def wav(p, n=800):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with wave.open(p, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
        w.writeframes(bytes(n * 2))


class Base(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        self.c = os.path.join(self.t.name, "ciclo")
        self.r = os.path.join(self.t.name, "raw")
        os.makedirs(os.path.join(self.c, "upload", "positive"))
        self.itens = [
            {"nome": "100000", "dia": "20261105", "status": "ok", "lado": "train", "episodio": "a"},
            {"nome": "100010", "dia": "20261105", "status": "ok", "lado": "holdout", "episodio": "b"},
            {"nome": "100020", "dia": "20261105", "status": "pendente_escuta", "lado": "train", "episodio": "c"},
            {"nome": "100030", "dia": "20261105", "status": "excluida", "lado": None, "episodio": "d"},
        ]
        for i in self.itens:
            with wave.open(os.path.join(self._t(), f"{i['nome']}.wav"), "wb") as w:
                w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
                w.writeframes(i["nome"].encode() * 100)
        self._salvar()

    def _t(self):
        d = os.path.join(self.r, "treino"); os.makedirs(d, exist_ok=True); return d

    def _salvar(self):
        json.dump({"itens": self.itens}, open(os.path.join(self.r, "manifesto.json"), "w"))

    def tearDown(self):
        self.t.cleanup()

    def test_so_ok_entram_e_cada_um_no_seu_lado(self):
        self.assertEqual(a.main([self.c, self.r]), 0)
        self.assertEqual(os.listdir(os.path.join(self.c, "upload", "positive")), ["ambiente__20261105_100000.wav"])
        self.assertEqual(os.listdir(os.path.join(self.c, "holdout_native", "ambiente")), ["ambiente__20261105_100010.wav"])

    def test_nao_apaga_o_registro_dos_nativos_e_e_idempotente(self):
        n = os.path.join(self.c, "native.ndjson")
        open(n, "w").write(json.dumps({"etq": "cv27", "nome": "x"}) + "\n")
        a.main([self.c, self.r]); a.main([self.c, self.r])
        linhas = [json.loads(l) for l in open(n)]
        self.assertEqual(sorted(l["etq"] for l in linhas), ["ambiente", "ambiente", "cv27"])

    def test_episodio_nos_dois_lados_recusa(self):
        self.itens[1]["episodio"] = "a"; self._salvar()
        self.assertEqual(a.main([self.c, self.r]), 3)
        self.assertFalse(os.listdir(os.path.join(self.c, "upload", "positive")))

    def test_mesmo_audio_nos_dois_lados_recusa(self):
        import shutil
        shutil.copy(os.path.join(self._t(), "100000.wav"), os.path.join(self._t(), "100010.wav"))
        self.assertEqual(a.main([self.c, self.r]), 3)

    def test_sem_upload_recusa(self):
        self.assertEqual(a.main([os.path.join(self.t.name, "nada"), self.r]), 2)

    def test_conta_no_counts(self):
        a.main([self.c, self.r])
        c = json.load(open(os.path.join(self.c, "counts.json")))
        self.assertEqual(c["positivos_ambiente"]["train"], 1)
        self.assertEqual(c["positivos_ambiente"]["holdout"], 1)
        self.assertEqual(c["positivos_ambiente"]["fora"], 2)


if __name__ == "__main__":
    unittest.main()
