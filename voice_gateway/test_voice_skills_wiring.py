"""Testes estáticos da ligação das habilidades ensinadas no bot.py (o bot não sobe nos testes).
cd /srv/friday/app && python3 -m unittest test_voice_skills_wiring -v"""
import os, py_compile, tempfile, unittest

AQUI = os.path.dirname(os.path.abspath(__file__))


def bot_py():
    with open(os.path.join(AQUI, "bot.py"), encoding="utf-8") as f:
        return f.read()


class TestFiacao(unittest.TestCase):
    def test_rotas_existem(self):
        t = bot_py()
        for r in ('@app.get("/voice_skills")', '@app.get("/voice_skills/tools")', '@app.post("/voice_skills")',
                  '@app.delete("/voice_skills/{key}")'):
            self.assertIn(r, t)

    def test_ferramentas_de_habilidade_condicionais(self):
        t = bot_py()
        self.assertIn("skill_tools=bool(habilidades)", t)
        self.assertIn("search_tool=cortou", t)

    def test_frase_de_grupo_sai_sempre(self):
        self.assertIn("ic.sem_frase_de_grupo(", bot_py())

    def test_diretrizes_pela_vista(self):
        t = bot_py()
        self.assertIn("vd.load()", t)
        self.assertNotIn("pv.ler()", t)

    def test_demonstracao_vai_ao_cerebro(self):
        self.assertIn("vs.demonstration_block(chamadas)", bot_py())

    def test_rotas_exigem_token(self):
        t = bot_py()
        trecho = t[t.index('@app.get("/voice_skills")'):t.index('@app.post("/reminder")')]
        self.assertEqual(trecho.count("_autorizado(request)"), 4)

    def test_bot_compila(self):
        with tempfile.TemporaryDirectory() as d:
            py_compile.compile(os.path.join(AQUI, "bot.py"), cfile=os.path.join(d, "bot.pyc"), doraise=True)


if __name__ == "__main__":
    unittest.main()
