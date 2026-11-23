"""cd satellite/program && python3 -m unittest test_negativos -v"""
import json, os, tempfile, unittest
import negativos as ng

SEG = ng.TAXA * ng.BYTES_AMOSTRA


def wav(pasta, nome, segundos):
    caminho = os.path.join(pasta, nome)
    with open(caminho, "wb") as f:
        f.write(b"\0" * (ng.CABECALHO + int(segundos * SEG)))
    return caminho


class Base(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.addCleanup(self._t.cleanup)
        self.r = self._t.name
        self.amb, self.base, self.acervo = (os.path.join(self.r, d) for d in ("amb", "base", "acervo"))
        for d in (self.amb, self.base, self.acervo):
            os.makedirs(d)

    def escolher(self):
        return ng.escolher(self.amb, self.base, self.acervo)


class TestEspalhar(Base):
    def test_clipes_igualmente_espacados_ate_o_alvo(self):
        arqs = [wav(self.amb, f"ambiente_202611{d:02d}_080000.wav", 300) for d in range(1, 11)]
        escolhidos = ng.espalhar(arqs, 900)
        self.assertEqual(len(escolhidos), 3)
        self.assertEqual(escolhidos, sorted(escolhidos))
        self.assertGreater(arqs.index(escolhidos[1]) - arqs.index(escolhidos[0]), 1)

    def test_sem_arquivos_ou_sem_alvo(self):
        self.assertEqual(ng.espalhar([], 900), [])
        self.assertEqual(ng.espalhar([wav(self.amb, "a.wav", 10)], 0), [])


class TestEscolher(Base):
    def test_ambiente_proprio_suficiente_dispensa_a_linha_de_base(self):
        for d in range(1, 9):
            wav(self.amb, f"ambiente_202611{d:02d}_080000.wav", 300)  # 40 min
        wav(self.base, "sat-aaaaaa__ambiente_20261101_080000.wav", 300)
        r = self.escolher()
        self.assertEqual(r["origem"], "ambiente")
        self.assertEqual(r["baseline_s"], 0)
        self.assertTrue(all(g.startswith("n:p:") for _, g in r["clipes"]))

    def test_sem_ambiente_proprio_usa_so_a_linha_de_base(self):
        for d in range(1, 6):
            wav(self.base, f"sat-aaaaaa__ambiente_202611{d:02d}_080000.wav", 300)
            wav(self.base, f"sat-bbbbbb__ambiente_202611{d:02d}_080000.wav", 300)
        r = self.escolher()
        self.assertEqual(r["origem"], "baseline")
        self.assertGreaterEqual(r["baseline_s"], 600)
        self.assertEqual(r["proprio_s"], 0)
        self.assertIsNone(ng.veredito(r))

    def test_pouco_ambiente_proprio_completa_com_a_linha_de_base(self):
        wav(self.amb, "ambiente_20261123_080000.wav", 300)
        for d in range(1, 6):
            wav(self.base, f"sat-aaaaaa__ambiente_202611{d:02d}_080000.wav", 300)
        r = self.escolher()
        self.assertEqual(r["origem"], "misto")
        self.assertEqual(r["proprio_s"], 300)
        self.assertLessEqual(r["proprio_s"] + r["baseline_s"], ng.ALVO_NEGATIVOS_S + 300)

    def test_negativos_duros_dos_outros_satelites_entram_todos(self):
        for i in range(4):
            wav(self.base, f"sat-aaaaaa__falsa_2026111{i}_a.wav", 3)
        r = self.escolher()
        self.assertEqual(len([c for c, _ in r["clipes"] if "__falsa_" in c]), 4)

    def test_acervo_do_mk1_conta_no_total(self):
        wav(self.acervo, "20261014_a.wav", 400)
        r = self.escolher()
        self.assertEqual(r["acervo_s"], 400)
        self.assertEqual(r["origem"], "nenhuma")
        self.assertIsNone(ng.veredito(r))

    def test_grupo_pelo_dia_e_pela_origem(self):
        self.assertEqual(ng.grupo("/x/ambiente_20261123_080000.wav", "p"), "n:p:20261123")
        self.assertEqual(ng.grupo("/x/sat-aaaaaa__falsa_20261110_x.wav", "b:sat-aaaaaa"), "n:b:sat-aaaaaa:20261110")


class TestVeredito(Base):
    def test_satelite_novo_sem_nada_nao_aprova_e_diz_o_que_fazer(self):
        r = self.escolher()
        self.assertEqual(r["origem"], "nenhuma")
        motivo = ng.veredito(r)
        self.assertIn("faltam negativos", motivo)
        self.assertIn("tente de novo", motivo)

    def test_exatamente_no_minimo_aprova(self):
        self.assertIsNone(ng.veredito({"total_s": ng.MIN_NEGATIVOS_S}))
        self.assertIsNotNone(ng.veredito({"total_s": ng.MIN_NEGATIVOS_S - 1}))


class TestRotulos(Base):
    def test_ausente_ou_quebrado_vira_vazio(self):
        self.assertEqual(ng.carregar_rotulos(os.path.join(self.r, "nao_existe.json")), {"verdadeiras": [], "falsas": []})
        ruim = os.path.join(self.r, "ruim.json")
        with open(ruim, "w") as f:
            f.write("{")
        self.assertEqual(ng.carregar_rotulos(ruim), {"verdadeiras": [], "falsas": []})

    def test_existente_e_lido(self):
        ok = os.path.join(self.r, "r.json")
        with open(ok, "w") as f:
            json.dump({"verdadeiras": ["a"], "falsas": ["b"]}, f)
        self.assertEqual(ng.carregar_rotulos(ok)["falsas"], ["b"])


if __name__ == "__main__":
    unittest.main()
