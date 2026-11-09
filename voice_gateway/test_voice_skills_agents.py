"""Coerência do ensino da voz (23/10/2026): o fato fica num lugar só, o AGENTS.md da friday, e precisa bater com o
script do cérebro, a rota do bot e as chaves que o validador confere. python3 -m unittest test_voice_skills_agents"""
import os, re, unittest

import voice_skills as vs

AQUI = os.path.dirname(os.path.abspath(__file__))
WS = os.environ.get("FRIDAY_TEST_WS", "/srv/friday/home/.openclaw/workspace/friday")
AGENTS = f"{WS}/AGENTS.md"


def agents():
    with open(AGENTS, encoding="utf-8") as f:
        return f.read()


class TestEnsinoDaVoz(unittest.TestCase):
    def test_agents_cita_o_script_que_existe(self):
        m = re.search(r"(/\S*/bin/voice_skills\.sh)", agents())
        self.assertIsNotNone(m, "o AGENTS.md não cita o voice_skills.sh")
        self.assertTrue(os.path.exists(m.group(1)), m.group(1))

    def test_script_usa_a_rota_do_bot(self):
        with open(f"{WS}/bin/voice_skills.sh", encoding="utf-8") as f:
            sh = f.read()
        self.assertIn("/voice_skills", sh)
        self.assertIn("127.0.0.1:7860", sh)
        with open(os.path.join(AQUI, "bot.py"), encoding="utf-8") as f:
            self.assertIn('@app.post("/voice_skills")', f.read())

    def test_agents_cita_o_bloco_de_demonstracao(self):
        bloco = vs.demonstration_block([{"tool": "x", "args": {}, "ok": True}])
        prefixo = bloco.strip().lstrip("[").split(":")[0]
        self.assertEqual(prefixo, "chamadas de ferramenta que deram certo nesta conversa, em ordem")
        self.assertIn(prefixo, agents())

    def test_agents_diz_que_a_lista_nao_prova_o_resultado(self):
        # 08/11/2026: o cérebro leu a lista como prova e disse "já está tocando" sem chamar o HA
        txt = re.sub(r"\s+", " ", agents())
        self.assertIn("That list does not show that what the person asked for exists now", txt)
        self.assertIn("known only from a call you make in that turn or from the current state in Home Assistant", txt)

    def test_agents_cita_as_chaves_do_validador(self):
        txt = agents()
        for chave in ("name", "description", "steps", "ordered", "taught_by", "teaching", "original_request",
                      "replace_id"):
            self.assertRegex(txt, rf'`"?{chave}"?`', chave)  # `name` ou `"replace_id"` (citado com aspas do JSON)
        for valor in vs.TEACHING:
            self.assertIn(f"`{valor}`", txt, valor)


if __name__ == "__main__":
    unittest.main()
