"""cd /srv/friday/app && python3 -m unittest test_preferencias_voz -v"""
import os, tempfile, unittest
import preferencias_voz as pv

USER_MD = """# USER.md - User Model

Use one directive per entry:

```md
<!-- observed: YYYY-MM-DD | status: active -->

- Prefer concise progress updates during implementation work.
```

## Directives

<!-- observed: 2026-09-29 | status: active -->

- Always address the user as Alex.

<!-- observed: 2026-09-29 | status: superseded -->

- Always notify Alex when Bia is approaching home.

<!-- observed: 2026-10-06 | status: active -->

- Always resolve requests for devices to the current room where the command originated when contextually applicable.


## Related

- [Agent workspace](/concepts/agent-workspace)
"""


class TestDiretrizes(unittest.TestCase):
    def test_so_ativas_da_secao_directives(self):
        self.assertEqual(pv.diretrizes_ativas(USER_MD), [
            "Always address the user as Alex.",
            "Always resolve requests for devices to the current room where the command originated when contextually applicable."])

    def test_sem_secao_devolve_vazio(self):
        self.assertEqual(pv.diretrizes_ativas("# nada aqui"), [])


class TestContexto(unittest.TestCase):
    def test_informa_sem_ditar_e_lista(self):
        t = pv.contexto(["Always address the user as Alex."])
        self.assertIn("aprendeu", t)
        self.assertIn("- Always address the user as Alex.", t)

    def test_vazio_sem_texto(self):
        self.assertEqual(pv.contexto([]), "")

    def test_ler_arquivo_ausente_nao_quebra(self):
        self.assertEqual(pv.ler("/caminho/que/nao/existe/USER.md"), "")

    def test_ler_arquivo(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "USER.md")
            with open(p, "w") as f:
                f.write(USER_MD)
            self.assertIn("- Always address the user as Alex.", pv.ler(p))
            self.assertNotIn("Bia is approaching", pv.ler(p))


EXTRA = "\n<!-- observed: 2026-10-23 | status: active -->\n\n- Always activate devices room by room.\n"


class TestAllActive(unittest.TestCase):
    def test_tira_o_comentario_do_lucid(self):
        # 23/10: a vista do Lucid compara sem a tag; com ela, a diretriz viraria duplicada ou "perdida"
        texto = (USER_MD + "\n<!-- observed: 2026-10-24 | status: active -->\n\n"
                 "- Prefer X. <!-- lucid: date=2026-10-24 evidence=10:00 said_by=unknown source=voice -->\n")
        self.assertEqual(pv.all_active(texto)[-1], ("2026-10-24", "Prefer X."))

    def test_diretriz_depois_de_related_entra(self):
        r = [t for _, t in pv.all_active(USER_MD + EXTRA)]
        self.assertEqual(r, [
            "Always address the user as Alex.",
            "Always resolve requests for devices to the current room where the command originated when contextually applicable.",
            "Always activate devices room by room."])

    def test_exemplo_cercado_fica_fora(self):
        self.assertFalse([t for _, t in pv.all_active(USER_MD + EXTRA) if "Prefer concise progress updates" in t])

    def test_superseded_fica_fora(self):
        self.assertFalse([t for _, t in pv.all_active(USER_MD + EXTRA) if "Bia is approaching" in t])

    def test_observed_vem_junto(self):
        self.assertEqual(pv.all_active(USER_MD + EXTRA)[-1], ("2026-10-23", "Always activate devices room by room."))

    def test_ler_usa_arquivo_inteiro(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "USER.md")
            with open(p, "w") as f:
                f.write(USER_MD + EXTRA)
            self.assertIn("- Always activate devices room by room.", pv.ler(p))


if __name__ == "__main__":
    unittest.main()
