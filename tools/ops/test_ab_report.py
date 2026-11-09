"""Testes do relatório do A/B das vistas de memória (spec 2026-10-21 §5.6).
Rodar: cd /config/tools/friday_memoria && python3 -m unittest -q test_ab_report"""
import json
import unittest
from datetime import datetime, timedelta, timezone

import ab_report as ab

BRT = timezone(timedelta(hours=-3))


def dt(h, m=0, s=0):
    return datetime(2026, 10, 22, h, m, s, tzinfo=BRT)


def ev(event, session, arm, h, m=0, s=0, **extra):
    return json.dumps({"event": event, "ts": dt(h, m, s).isoformat(), "session": session, "arm": arm, **extra})


class TestParse(unittest.TestCase):
    def test_parse_ab(self):
        linhas = [ev("start", "a", "off", 9), ev("usage", "a", "off", 9, 1, prompt_tokens=9000, reasoning_tokens=100),
                  ev("usage", "a", "off", 9, 2, prompt_tokens=9100, reasoning_tokens=120), ev("end", "a", "off", 9, 5),
                  ev("start", "b", "on", 10), "{quebrada", ""]
        r = ab.parse_ab(linhas)
        self.assertEqual(set(r), {"a", "b"})
        self.assertEqual(r["a"]["arm"], "off")
        self.assertEqual((r["a"]["start"], r["a"]["end"]), (dt(9), dt(9, 5)))
        self.assertEqual((r["a"]["prompt"], r["a"]["reasoning"]), ([9000, 9100], [100, 120]))
        self.assertIsNone(r["b"]["end"])  # sessão sem end: a janela vai até o último evento

    def test_parse_espera(self):
        linha = ('2026-10-19T13:59:32.608479-03:00 friday-satellite python[103022]: {"espera": {"espera_resposta": 1.1, '
                 '"como_abriu": "palavra"}, "satelite": "sat-0a1b2c"}')
        r = ab.parse_espera([linha, "2026-10-19T14:00:00-03:00 x python[1]: outra coisa", "lixo",
                             '2026-10-19T14:00:01-03:00 x python[1]: {"espera": {"como_abriu": "palavra"}}'])
        self.assertEqual(r, [(datetime.fromisoformat("2026-10-19T13:59:32.608479-03:00"), 1.1)])


class TestJoin(unittest.TestCase):
    def test_join_por_janela(self):
        sessoes = {"a": {"arm": "off", "start": dt(9), "end": dt(9, 5), "reasoning": [100], "prompt": [9000]},
                   "b": {"arm": "on", "start": dt(10), "end": dt(10, 5), "reasoning": [110, 130], "prompt": [9500, 9600]}}
        esperas = [(dt(9, 2), 1.2), (dt(9, 30), 9.9), (dt(10, 1), 1.5), (dt(10, 2), 1.7)]
        r = ab.join(sessoes, esperas)
        self.assertEqual(r["off"]["espera"], [1.2])
        self.assertEqual(r["on"]["espera"], [1.5, 1.7])
        self.assertEqual((r["off"]["n"], r["on"]["n"]), (1, 1))
        self.assertEqual(r["on"]["reasoning"], [110, 130])
        self.assertEqual(r["on"]["prompt"], [9500, 9600])

    def test_join_sessao_sem_end_vai_ate_o_ultimo_evento(self):
        sessoes = {"a": {"arm": "on", "start": dt(9), "end": None, "last": dt(9, 10), "reasoning": [], "prompt": []}}
        r = ab.join(sessoes, [(dt(9, 8), 1.0), (dt(9, 20), 2.0)])
        self.assertEqual(r["on"]["espera"], [1.0])

    def test_join_bracos_vazios(self):
        r = ab.join({}, [])
        self.assertEqual((r["off"]["n"], r["on"]["n"]), (0, 0))


def braco(n=30, espera=1.0, rac=100, prompt=9000):
    return {"n": n, "espera": [espera] * n, "reasoning": [rac] * n, "prompt": [prompt] * n}


class TestVeredito(unittest.TestCase):
    def test_verdict_amostra_pequena(self):
        v = ab.verdict(braco(29), braco(30))
        self.assertIsNone(v["pass"])
        self.assertTrue(v["reasons"])

    def test_verdict_reprova_mediana(self):
        v = ab.verdict(braco(30, espera=1.0), braco(30, espera=1.4))
        self.assertIs(v["pass"], False)
        self.assertTrue(any("0,3" in r for r in v["reasons"]))

    def test_verdict_reprova_p90(self):
        on = braco(30, espera=1.0)
        on["espera"] = [1.0] * 20 + [2.0] * 10  # mediana igual, p90 +1,0 s
        v = ab.verdict(braco(30, espera=1.0), on)
        self.assertIs(v["pass"], False)
        self.assertTrue(any("0,5" in r for r in v["reasons"]))

    def test_verdict_reprova_raciocinio(self):
        v = ab.verdict(braco(30, rac=100), braco(30, rac=120))
        self.assertIs(v["pass"], False)
        self.assertTrue(any("15%" in r for r in v["reasons"]))

    def test_verdict_aprova(self):
        v = ab.verdict(braco(30), braco(30))
        self.assertIs(v["pass"], True)
        self.assertEqual(v["reasons"], [])

    def test_verdict_aprova_com_diferenca_dentro_do_limite(self):
        v = ab.verdict(braco(30, espera=1.0, rac=100), braco(30, espera=1.25, rac=114))
        self.assertIs(v["pass"], True)

    def test_verdict_sem_dados_de_espera_nao_aprova(self):
        a, b = braco(30), braco(30)
        a["espera"], b["espera"] = [], []
        self.assertIsNone(ab.verdict(a, b)["pass"])


class TestEstatistica(unittest.TestCase):
    def test_med_e_p90(self):
        self.assertEqual(ab.median([1, 2, 3]), 2)
        self.assertIsNone(ab.median([]))
        self.assertEqual(ab.p90([5]), 5)
        self.assertAlmostEqual(ab.p90(list(range(1, 11))), 9.9)


if __name__ == "__main__":
    unittest.main()
