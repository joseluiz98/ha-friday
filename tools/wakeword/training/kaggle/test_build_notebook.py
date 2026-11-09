"""cd /homeassistant/tools/satelite/wakeword-training/kaggle && python3 -m unittest -q test_build_notebook"""
import json, os, re, subprocess, sys, tempfile, unittest
import build_notebook as bn

QUI = os.path.dirname(os.path.abspath(__file__))


def marcas(nb):
    out = []
    for c in nb["cells"]:
        if c["cell_type"] != "code":
            continue
        out.append("".join(c["source"]).split("\n")[0])
    return out


class TestBuildNotebook(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.nb = bn.build()
        cls.src = ["".join(c["source"]) for c in cls.nb["cells"]]

    def test_ordem_das_marcas(self):
        esperado = ["# 0.", "# P. params", "# 1.", "# 2.", "# 3.", "# 4.", "# 5.", "# 6.", "# 7.", "# 8.", "# 9.",
                    "# 10.", "# 11.", "# 11b", "# 11c", "# 11d", "# 12.", "# 13.", "# 14.", "# 15.", "# 16."]
        primeiras = marcas(self.nb)
        self.assertEqual(len(primeiras), len(esperado))
        for lin, esp in zip(primeiras, esperado):
            self.assertTrue(lin.startswith(esp), f"{lin!r} não começa por {esp!r}")

    def test_exporta_todos_os_checkpoints(self):  # 07/11: a escolha sai do ACAV e vai para a VM
        treino = next(x for x in self.src if x.startswith("# 14."))
        self.assertIn("ALL_CKPTS = []", treino)
        self.assertIn("ALL_CKPTS.append((step_label", treino)
        exp = next(x for x in self.src if x.startswith("# 15."))
        self.assertIn("/kaggle/working/ckpts", exp)
        self.assertIn("ckpts.json", exp)
        self.assertLess(exp.index("metrics.json"), exp.index("ckpts.json"))

    def test_sem_google_colab(self):
        for s in self.src:
            self.assertNotIn("google.colab", s)

    def test_celula_10_parametrizada(self):
        c10 = [s for s in self.src if s.startswith("# 10.")][0]
        self.assertNotIn("'friday'", c10)
        self.assertNotIn("'sexta_feira'", c10)
        for chave in ("PARAMS['target_phrase']", "PARAMS['model_name']", "PARAMS['custom_negative_phrases']",
                      "PARAMS['n_samples']", "PARAMS['n_samples_val']", "PARAMS['augmentation_rounds']",
                      "PARAMS['steps']"):
            self.assertIn(chave, c10)

    def test_metrics_json_presente(self):
        self.assertTrue(any("/kaggle/working/metrics.json" in s for s in self.src))
        c15 = [s for s in self.src if s.startswith("# 15.")][0]
        self.assertIn("qualified = []", c15)
        for campo in ("n_checkpoints", "val_recall", "val_fp_per_hour", "val_accuracy", "pos_test_recall_at_0_5",
                      "n_real_pos", "n_real_neg", "n_ptbr"):
            self.assertIn(campo, c15)

    def test_celula_11c_remove_clipes_ruins(self):
        src = ["".join(c["source"]) for c in bn.build()["cells"]]
        c11c = [s for s in src if s.startswith("# 11c")][0]
        self.assertIn("os.remove(_f)", c11c)
        self.assertIn(">= 0.25", c11c)

    def test_celula_16_usa_model_name(self):
        c16 = [s for s in self.src if s.startswith("# 16.")][0]
        self.assertIn("MODEL_NAME", c16)
        self.assertNotIn("friday.onnx", c16)

    def test_celulas_de_codigo_compilam(self):
        for c in self.nb["cells"]:
            if c["cell_type"] != "code":
                continue
            s = "".join(c["source"])
            if True:
                linhas = [l for l in s.split("\n") if not l.lstrip().startswith(("!", "%"))]
                # magias de shell (!cmd) fora; o resto precisa ser Python válido (continuações de "!" ficam de fora)
                texto = "\n".join(l for l in linhas if not l.rstrip().endswith("\\") and not l.startswith("        --"))
                try:
                    compile(texto, "cell", "exec")
                except SyntaxError as e:
                    if "!" in s:  # células com magia: validadas no Kaggle
                        continue
                    self.fail(f"{s.splitlines()[0]}: {e}")

    def test_idempotente(self):
        a = json.dumps(bn.build(), sort_keys=True)
        b = json.dumps(bn.build(), sort_keys=True)
        self.assertEqual(a, b)

    def test_arquivo_gerado_igual_ao_build(self):
        with tempfile.TemporaryDirectory() as d:
            alvo = os.path.join(d, "x.ipynb")
            subprocess.run([sys.executable, "-I", os.path.join(QUI, "build_notebook.py"), alvo], check=True)
            self.assertEqual(json.load(open(alvo)), bn.build())

    def test_substituicao_falha_se_o_texto_nao_existe(self):
        with self.assertRaises(AssertionError):
            bn.sub("abc", "xyz", "q")


class TestNegativosDoParams(unittest.TestCase):
    """02/11: fração do ACAV e peso máximo dos negativos vêm do params.json (padrões = os valores de antes)."""
    @classmethod
    def setUpClass(cls):
        cls.src = ["".join(c["source"]) for c in bn.build()["cells"]]

    def test_fracao_do_acav(self):
        c7 = next(s for s in self.src if s.startswith("# 7."))
        self.assertIn("n_train = max(1, round(N * PARAMS.get('acav_train_frac', 0.1)))", c7)
        self.assertNotIn("N // 10,", c7)

    def test_peso_maximo_dos_negativos(self):
        c10 = next(s for s in self.src if s.startswith("# 10."))
        self.assertIn("'max_negative_weight': PARAMS.get('max_negative_weight', 1500),", c10)


class TestPatchG(unittest.TestCase):
    def test_peso_por_etiqueta(self):  # 26/10
        ns = {}
        exec(bn.COPIAS, ns)
        self.assertEqual(ns["copias"]("sessao__tentativas_friday_music_x.wav_at_1.00.wav", 10, {"_music_": 30}), 30)
        self.assertEqual(ns["copias"]("ativou__20261020.wav_at_1.00.wav", 10, {"_music_": 30}), 10)
        self.assertEqual(ns["copias"]("qualquer.wav", 3, {}), 3)
        src = ["".join(c["source"]) for c in bn.build()["cells"]]
        c11c = [s for s in src if s.startswith("# 11c")][0]
        self.assertIn("real_copies_by_tag", c11c)
        self.assertIn("def copias(", c11c)

    def test_trava_da_marca_na_celula_p(self):  # 26/10: dataset velho para no 1º minuto
        src = ["".join(c["source"]) for c in bn.build()["cells"]]
        cp = [s for s in src if s.startswith("# P.")][0]
        self.assertIn("upload_id", cp)
        self.assertIn("assert _m is None or", cp)
        rx = re.search(r"re\.search\(r'(.+?)'", cp).group(1)
        self.assertEqual(re.search(rx, "cycle-20261026b-20261026230000-541p704n").groups(), ("541", "704"))

    def test_negativos_ptbr_opcionais(self):  # 27/10
        src = ["".join(c["source"]) for c in bn.build()["cells"]]
        c11d = [s for s in src if s.startswith("# 11d")][0]
        self.assertIn("PARAMS.get('ptbr_negatives')", c11d)
        self.assertIn("sext", c11d)
        c15 = [s for s in src if s.startswith("# 15.")][0]
        self.assertIn("n_pt_neg", c15)

    def test_reamostragem_confere_todos_os_arquivos(self):  # 27/10: amostra de 20 pulava a pasta mista
        src = ["".join(c["source"]) for c in bn.build()["cells"]]
        c12 = [s for s in src if s.startswith("# 12.")][0]
        self.assertNotIn("files[:20]", c12)
        self.assertIn("for f in files:", c12)

    def test_pronuncia_da_sexta_feira(self):
        nb = bn.build()
        fonte = "".join("".join(c["source"]) for c in nb["cells"])
        self.assertIn("Patch G", fonte)
        self.assertIn('"fayra": ["F EY1 R AH0"]', fonte)
        self.assertIn("All 7 patches applied", fonte)
        self.assertNotIn("All 6 patches applied", fonte)


class TestNegativosPtBr(unittest.TestCase):  # 06/11: frases parecidas com voz nativa
    @classmethod
    def setUpClass(cls):
        cls.c11b = [s for s in ["".join(c["source"]) for c in bn.build()["cells"]] if s.startswith("# 11b")][0]
        cls.params = json.load(open(os.path.join(QUI, "..", "params", "sexta_feira.json")))

    def test_compila(self):
        compile(self.c11b, "11b", "exec")

    def test_negativos_vao_para_as_pastas_de_negativos(self):
        for chave in ("cfg['negative_clips_train_dir']", "cfg['negative_clips_test_dir']", "'prefix': 'ptbrneg_'"):
            self.assertIn(chave, self.c11b)

    def test_cada_job_usa_os_proprios_textos(self):
        self.assertIn("job.get('textos', spec['textos'])", self.c11b)

    def test_palavra_friday_sem_a_lista_nao_muda(self):
        self.assertIn("PARAMS.get('ptbr_neg_texts')", self.c11b)
        self.assertNotIn("ptbr_neg_texts", json.load(open(os.path.join(QUI, "..", "params", "friday.json"))))

    def test_nenhum_negativo_e_a_propria_palavra(self):
        alvo = re.compile(r"(sexta|sesta|esta|desta|nesta|testa)[- ]?feira", re.I)
        for t in self.params["ptbr_neg_texts"]:
            self.assertIsNone(alvo.search(t), t)

    def test_celula_recusa_a_propria_palavra(self):
        self.assertIn("assert not any(re.search(r'(sexta|sesta)[- ]?feira'", self.c11b)

    def test_metricas_contam(self):
        c15 = [s for s in ["".join(c["source"]) for c in bn.build()["cells"]] if s.startswith("# 15.")][0]
        self.assertIn("n_ptbr_neg", c15)


if __name__ == "__main__":
    unittest.main()
