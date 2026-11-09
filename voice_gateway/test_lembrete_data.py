"""Datas dos lembretes (23/10/2026): a voz não sabe a data de hoje e o cérebro sabe. Em 22/10 às 23:20 a voz trocou o
"amanhã" do Alex por "24 de outubro" e o lembrete foi para o dia errado. Os dois lados precisam dizer o mesmo fato:
a voz repassa datas nas palavras do Alex, o cérebro resolve, confere e diz o dia da semana.
python3 -m unittest test_lembrete_data"""
import os, re, unittest

AQUI = os.path.dirname(os.path.abspath(__file__))
WS = os.environ.get("FRIDAY_TEST_WS", "/srv/friday/home/.openclaw/workspace/friday")
AGENTS = f"{WS}/AGENTS.md"


def descricao_do_pedido():
    src = open(os.path.join(AQUI, "bot.py"), encoding="utf-8").read()
    m = re.search(r'"pedido": \{\s*"type": "string",\s*"description": \((.*?)\),\s*\}', src, re.S)
    if not m:
        m = re.search(r'"pedido": \{\s*"type": "string",\s*"description": (".*?"),\s*\}', src, re.S)
    return "".join(re.findall(r'"((?:[^"\\]|\\.)*)"', m.group(1))) if m else ""


def fato_das_datas():
    txt = open(AGENTS, encoding="utf-8").read()
    m = re.search(r"^- Dates in reminder requests:.*?(?=^- |^#)", txt, re.M | re.S)
    return m.group(0) if m else ""


class TestDataDoLembrete(unittest.TestCase):
    def test_voz_sabe_que_nao_sabe_a_data(self):
        d = descricao_do_pedido()
        self.assertIn("não sabe a data de hoje", d)
        self.assertIn("amanhã", d)

    def test_voz_nao_pede_para_completar_o_pedido(self):
        # "com todo o contexto necessário" era o convite para a voz completar a data por conta própria
        self.assertNotIn("todo o contexto necessário", descricao_do_pedido())

    def test_cerebro_tem_o_fato(self):
        f = fato_das_datas()
        self.assertTrue(f, "o AGENTS.md não tem o fato das datas dos lembretes")
        for trecho in ("does not know today's date", "disagree", "midnight", "weekday"):
            self.assertIn(trecho, f)

    def test_fato_num_lugar_so(self):
        self.assertEqual(open(AGENTS, encoding="utf-8").read().count("Dates in reminder requests"), 1)


if __name__ == "__main__":
    unittest.main()
