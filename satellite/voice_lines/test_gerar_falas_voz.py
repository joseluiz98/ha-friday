"""A voz das falas geradas é a da F.R.I.D.A.Y. de produção (FRIDAY_VOICE=Aoede no friday.env da VM), sem o poc.env.
Rodar: cd satellite/voice_lines && python3 -m unittest -q test_gerar_falas_voz"""
import ast, unittest

import os
AQUI = os.path.dirname(os.path.abspath(__file__))
FONTE = os.path.join(AQUI, "gerar_falas_com_voz_da_friday.py")


class TestVozDasFalas(unittest.TestCase):
    def test_voz_de_producao_sem_poc_env(self):
        t = open(FONTE).read()
        self.assertNotIn("openclaw-poc", t)
        consts = {n.targets[0].id: n.value.value for n in ast.parse(t).body
                  if isinstance(n, ast.Assign) and isinstance(n.value, ast.Constant) and isinstance(n.targets[0], ast.Name)}
        self.assertEqual(consts.get("VOZ_DA_FRIDAY"), "Aoede")

    def test_falas_nao_expoem_o_mecanismo(self):
        """23/11: a pessoa ouve uma só assistente, em primeira pessoa (skill designing-for-friday, item 14)."""
        import gerar_falas_aprendizagem as g
        for nome, texto in g.FALAS.items():
            for interno in ("satélite", "cérebro", "modelo", "servidor"):
                self.assertNotIn(interno, texto.lower(), nome)

    def test_fala_livre_vale_para_uma_ou_varias_palavras(self):
        import gerar_falas_aprendizagem as g
        self.assertNotIn("Friday", g.FALAS["aprendizagem_fala_livre"])
        self.assertNotIn("Sexta", g.FALAS["aprendizagem_fala_livre"])

    def test_uma_intro_por_palavra_extra(self):
        import gerar_falas_aprendizagem as g
        self.assertIn("Sexta-feira", g.FALAS["aprendizagem_intro_sexta_feira"])


if __name__ == "__main__":
    unittest.main()
