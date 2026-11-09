import json, os, tempfile, unittest

import build_xtts_neg as b


class TestNotebook(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.nb = b.build("marca-x", "run-y", 2)
        cls.src = ["".join(c["source"]) for c in cls.nb["cells"]]

    def test_celulas_compilam(self):
        for i, s in enumerate(self.src):
            compile(s, f"c{i}", "exec")

    def test_confere_a_marca(self):
        self.assertIn("MARCA_ESPERADA = 'marca-x'", self.src[1])
        self.assertIn("RUN_ID = 'run-y'", self.src[1])

    def test_textos_e_filtro_embutidos(self):
        self.assertIn("Quarta-feira", self.src[2])
        self.assertIn("def aceita", self.src[3])
        self.assertIn("xttsneg__", self.src[3])

    def test_filtro_embutido_funciona(self):
        ns = {}
        inicio = self.src[3].index("import difflib")
        fim = self.src[3].index("try:\n    asr")
        exec(self.src[3][inicio:fim], ns)
        self.assertFalse(ns["aceita"]("Quinta-feira", "Sexta-feira."))
        self.assertTrue(ns["aceita"]("Quinta-feira", "Quinta-feira."))

    def test_kernel_proprio_nao_sobrescreve_os_positivos(self):
        self.assertEqual(b.meta()["id"], "joseluiz98/xtts-sexta-neg-gen")
        self.assertTrue(b.meta()["is_private"])


if __name__ == "__main__":
    unittest.main()
