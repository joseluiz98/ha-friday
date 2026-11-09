"""cd /srv/friday/app && python3 -m unittest test_voice_directives -v"""
import os, tempfile, unittest
import preferencias_voz as pv
import voice_directives as vd


def md(*itens):
    """itens: (observed, texto[, status])"""
    out = "# USER.md\n\n## Directives\n\n"
    for it in itens:
        status = it[2] if len(it) > 2 else "active"
        out += f"<!-- observed: {it[0]} | status: {status} -->\n\n- {it[1]}\n\n"
    return out


class TestBuild(unittest.TestCase):
    def test_sem_vista_entrega_todas(self):
        u = md(("2026-09-29", "A"), ("2026-10-01", "B"), ("2026-10-23", "C"))
        r = vd.build(u, None, None, [])
        self.assertEqual(r.delivered, ["A", "B", "C"])
        self.assertEqual((r.overflow, r.missing), ([], []))

    def test_vista_mais_delta_do_dia(self):
        u = md(("2026-09-29", "A"), ("2026-10-01", "B"), ("2026-10-23", "C"))
        r = vd.build(u, "- A\n- B\n", "2026-10-23", [])
        self.assertEqual(r.delivered, ["A", "B", "C"])
        self.assertIn("- C", r.context)

    def test_delta_nao_duplica_o_que_ja_esta_na_vista(self):
        u = md(("2026-09-29", "A"), ("2026-10-23", "C"))
        r = vd.build(u, "- A\n- C\n", "2026-10-23", [])
        self.assertEqual(r.delivered.count("C"), 1)

    def test_delta_teto_5_e_mais_recentes_primeiro(self):
        u = md(*[("2026-10-23", f"D{i}") for i in range(7)])
        r = vd.build(u, "", "2026-10-23", [])
        self.assertEqual(len(r.delivered), 5)
        self.assertEqual(set(r.delivered), {"D2", "D3", "D4", "D5", "D6"})
        self.assertEqual(len(r.overflow), 2)

    def test_delta_teto_600_caracteres(self):
        u = md(("2026-10-23", "x" * 400), ("2026-10-23", "y" * 400))
        r = vd.build(u, "", "2026-10-23", [])
        self.assertEqual(len(r.delivered), 1)
        self.assertEqual(len(r.overflow), 1)

    def test_vista_cortada_no_teto(self):
        linhas = "".join(f"- {chr(97 + i)}" + "z" * 58 + "\n" for i in range(26)) * 2
        r = vd.build("", linhas, "2026-10-23", [])
        self.assertLessEqual(sum(len(t) for t in r.delivered), vd.VIEW_MAX)
        self.assertGreater(len(r.delivered), 0)

    def test_delta_pula_excluidas(self):
        u = md(("2026-10-23", "C"))
        r = vd.build(u, "", "2026-10-23", ["C"])
        self.assertEqual(r.delivered, [])
        self.assertEqual(r.missing, [])

    def test_missing_aponta_ativa_perdida(self):
        u = md(("2026-09-29", "D"))
        self.assertEqual(vd.build(u, "- A\n", "2026-10-23", []).missing, ["D"])
        self.assertEqual(vd.build(u, "- A\n", "2026-10-23", ["D"]).missing, [])


class TestLoad(unittest.TestCase):
    def test_load_sem_arquivos_nao_quebra(self):
        old = (pv.CAMINHO, vd.VIEW, vd.EXCLUDED)
        pv.CAMINHO, vd.VIEW, vd.EXCLUDED = "/nao/existe/U.md", "/nao/existe/v.md", "/nao/existe/e.json"
        try:
            r = vd.load()
        finally:
            pv.CAMINHO, vd.VIEW, vd.EXCLUDED = old
        self.assertEqual(r.context, "")

    def test_load_com_arquivos(self):
        old = (pv.CAMINHO, vd.VIEW, vd.EXCLUDED)
        with tempfile.TemporaryDirectory() as d:
            u, v, e = (os.path.join(d, n) for n in ("U.md", "v.md", "e.json"))
            for caminho, texto in ((u, md(("2026-09-29", "A"), ("2999-01-01", "N"))), (v, "- A\n"), (e, "{bad json")):
                with open(caminho, "w") as f:
                    f.write(texto)
            pv.CAMINHO, vd.VIEW, vd.EXCLUDED = u, v, e
            try:
                r = vd.load()
            finally:
                pv.CAMINHO, vd.VIEW, vd.EXCLUDED = old
        self.assertEqual(r.delivered, ["A", "N"])


if __name__ == "__main__":
    unittest.main()
