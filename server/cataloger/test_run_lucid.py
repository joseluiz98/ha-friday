"""Testes do runner do Lucid Dreaming (run_lucid.py): chamada única ao Gemini, gravação pelo código."""
import io
import json
import os
import tempfile
import unittest
import urllib.error
from pathlib import Path

import run_lucid as rl

TAG = "<!-- lucid: date=2026-10-20 evidence={ev} said_by=unknown source=voice -->"

USER_MD = """# USER.md - User Model

Store stable user preferences.

```md
<!-- observed: YYYY-MM-DD | status: active -->

- Prefer concise progress updates during implementation work.
```

- Begin each directive with an imperative.

## Directives

<!-- observed: 2026-09-29 | status: active -->

- Always address the user as Alex.

<!-- observed: 2026-09-29 | status: superseded -->

- Prefer turning on the office computer automatically.

<!-- observed: 2026-10-08 | status: active -->

- Always resolve device requests prioritizing the current room.

<!-- observed: 2026-10-13 | status: superseded -->

- Always show time in UTC.


## Related

- [Agent workspace](/concepts/agent-workspace)
"""


class TestPuras(unittest.TestCase):
    def test_lost_lines_ignora_linhas_lucid(self):
        antes = "A\n- fato <!-- lucid: x -->\nB"
        self.assertEqual(rl.lost_lines(antes, "A\nB"), [])
        self.assertEqual(rl.lost_lines(antes, "A"), ["B"])

    def test_lost_lines_ignora_vazias(self):
        self.assertEqual(rl.lost_lines("A\n\n\nB\n", "A\nB\n"), [])

    def test_lucid_count(self):
        txt = "a\n- x <!-- lucid: date=2026-10-19 -->\nb\n- y <!-- lucid: date=2026-10-20 -->\n"
        self.assertEqual(rl.lucid_count(txt), 2)
        self.assertEqual(rl.lucid_count(""), 0)

    def test_record_tem_todas_as_chaves(self):
        r = rl.record("2026-10-19", "a", "b", 3, 1, 2, 1, 0, 0, 2, 3000, 800, 200, 0.012, "ok")
        self.assertEqual(sorted(r), sorted([
            "date", "started_at", "finished_at", "turns_in", "lucid_lines_before", "lucid_lines_after",
            "facts_added", "facts_corrected", "directives_changed", "discarded", "user_md_chars",
            "briefing_chars", "capabilities_chars", "cost_usd", "status"]))
        self.assertEqual(r["status"], "ok")

    def test_build_request(self):
        b = rl.build_request("SISTEMA", "mem", "usr", "- s: d", "2026-10-20", "linha1")
        self.assertEqual(b["systemInstruction"]["parts"][0]["text"], "SISTEMA")
        texto = b["contents"][0]["parts"][0]["text"]
        self.assertEqual(b["contents"][0]["role"], "user")
        self.assertTrue(texto.startswith("# MEMORY.md atual\n"))
        self.assertIn("# Conversas de voz de 2026-10-20 (horário de Brasília)\n", texto)
        self.assertEqual(b["generationConfig"], {"responseMimeType": "application/json"})
        self.assertNotIn("tools", b)

    def test_parse_result_rejeita_formato_errado(self):
        ok = {"facts": [], "directives": [], "voice_briefing": "v", "brain_capabilities": "c"}
        self.assertEqual(rl.parse_result(json.dumps(ok))["discarded"], [])
        for ruim in ("não é json", json.dumps({**ok, "facts": "x"}),
                     json.dumps({**ok, "facts": [{"replaces": None, "evidence": []}]}),
                     json.dumps({**ok, "facts": [{"text": "", "replaces": None, "evidence": []}]}),
                     json.dumps({**ok, "directives": [{"text": "a", "replaces": 3, "evidence": []}]}),
                     json.dumps({**ok, "directives": [{"text": "a", "replaces": None, "evidence": [1]}]}),
                     json.dumps({**ok, "voice_briefing": 3}),
                     json.dumps({**ok, "discarded": "x"}), "[]"):
            with self.assertRaises(ValueError, msg=ruim):
                rl.parse_result(ruim)

    def test_usage_cost(self):
        self.assertAlmostEqual(rl.usage_cost({"promptTokenCount": 1_000_000, "cachedContentTokenCount": 0,
                                              "candidatesTokenCount": 0, "thoughtsTokenCount": 0}), 0.75)
        self.assertAlmostEqual(rl.usage_cost({"promptTokenCount": 10000, "candidatesTokenCount": 1000,
                                              "thoughtsTokenCount": 1000}), 0.015)
        self.assertEqual(rl.usage_cost({}), 0.0)

    def test_response_text(self):
        resp = {"candidates": [{"content": {"parts": [{"text": "a"}, {"text": "b"}]}}]}
        self.assertEqual(rl.response_text(resp), "ab")

    def test_cut(self):
        self.assertEqual(rl.cut("um dois três", 8), "um dois")
        self.assertEqual(rl.cut("  curto \n", 50), "curto")
        self.assertEqual(rl.cut("um dois três", 7), "um dois")

    def test_lucid_tag(self):
        self.assertEqual(rl.lucid_tag("2026-10-20", ["08:10", "08:11"]),
                         "<!-- lucid: date=2026-10-20 evidence=08:10,08:11 said_by=unknown source=voice -->")
        self.assertIn("evidence= said_by", rl.lucid_tag("2026-10-20", []))


class _Resp:
    def __init__(self, data):
        self._d = data

    def read(self):
        return self._d

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _http_error(code, corpo=b"erro"):
    return urllib.error.HTTPError("http://x", code, "msg", {}, io.BytesIO(corpo))


class TestCallGemini(unittest.TestCase):
    def test_call_gemini_repete_503_e_desiste(self):
        chamadas, dormiu = [], []

        def urlopen(req, timeout=None):
            chamadas.append(req)
            raise _http_error(503, b"high demand SEGREDO")

        with self.assertRaises(rl.LucidCallError) as cm:
            rl.call_gemini({"a": 1}, "SEGREDO", urlopen=urlopen, sleep=dormiu.append)
        self.assertEqual(len(chamadas), 3)
        self.assertEqual(dormiu, [10, 20])
        self.assertNotIn("SEGREDO", str(cm.exception))
        self.assertIn("503", str(cm.exception))

    def test_call_gemini_400_nao_repete(self):
        dormiu = []

        def urlopen(req, timeout=None):
            raise _http_error(400)

        with self.assertRaises(rl.LucidCallError):
            rl.call_gemini({}, "SEGREDO", urlopen=urlopen, sleep=dormiu.append)
        self.assertEqual(dormiu, [])

    def test_call_gemini_manda_chave_no_cabecalho(self):
        vistos = []

        def urlopen(req, timeout=None):
            vistos.append((req, timeout))
            return _Resp(json.dumps({"ok": 1}).encode())

        r = rl.call_gemini({"x": 1}, "SEGREDO", urlopen=urlopen, sleep=lambda s: None)
        self.assertEqual(r, {"ok": 1})
        req, timeout = vistos[0]
        self.assertEqual(req.get_header("X-goog-api-key"), "SEGREDO")
        self.assertNotIn("SEGREDO", req.full_url)
        self.assertIn("gemini-3.8-flash:generateContent", req.full_url)
        self.assertEqual(timeout, 600)

    def test_call_gemini_503_e_depois_sucesso(self):
        estado = {"n": 0}

        def urlopen(req, timeout=None):
            estado["n"] += 1
            if estado["n"] < 2:
                raise _http_error(429)
            return _Resp(b"{}")

        self.assertEqual(rl.call_gemini({}, "k", urlopen=urlopen, sleep=lambda s: None), {})


class TestFatos(unittest.TestCase):
    def test_fato_novo_cria_secao_no_fim(self):
        mem = "# Memória\nlinha um\n"
        novo, add, cor = rl.apply_facts(
            mem, [{"text": "A sala tem dois Echos.", "replaces": None, "evidence": ["08:10"]}], "2026-10-20")
        self.assertTrue(novo.endswith(
            "\n## Aprendido nas conversas de voz\n- A sala tem dois Echos. "
            "<!-- lucid: date=2026-10-20 evidence=08:10 said_by=unknown source=voice -->\n"))
        self.assertTrue(novo.startswith(mem))
        self.assertEqual((add, cor), (1, 0))

    def test_secao_nova_sem_linha_em_branco_dupla(self):
        novo, _, _ = rl.apply_facts("a\n\n", [{"text": "F.", "replaces": None, "evidence": []}], "2026-10-20")
        self.assertEqual(novo.count("\n\n\n"), 0)
        self.assertTrue(novo.startswith("a\n\n## Aprendido"))

    def test_fato_novo_entra_no_fim_da_secao_existente(self):
        antes = ("# M\n\n## Aprendido nas conversas de voz\n- Velho. " + TAG.format(ev="07:00") +
                 "\n\n## Outra\ntexto\n")
        depois, add, cor = rl.apply_facts(
            antes, [{"text": "Novo.", "replaces": None, "evidence": ["09:00"]}], "2026-10-20")
        linhas = depois.split("\n")
        i = linhas.index("## Aprendido nas conversas de voz")
        self.assertTrue(linhas[i + 1].startswith("- Velho."))
        self.assertTrue(linhas[i + 2].startswith("- Novo."))
        self.assertEqual(linhas[i + 3], "")
        self.assertEqual(linhas[i + 4], "## Outra")
        self.assertEqual(rl.lost_lines(antes, depois), [])
        self.assertEqual((add, cor), (1, 0))

    def test_fato_repetido_e_ignorado(self):
        antes = "# M\n\n## Aprendido nas conversas de voz\n- Velho. " + TAG.format(ev="07:00") + "\n"
        depois, add, cor = rl.apply_facts(
            antes, [{"text": "Velho.", "replaces": None, "evidence": ["09:00"]}], "2026-10-20")
        self.assertEqual((depois, add, cor), (antes, 0, 0))

    def test_correcao_troca_so_dentro_da_secao(self):
        antes = ("# M\n- Fora da secao.\n\n## Aprendido nas conversas de voz\n- Velho. " + TAG.format(ev="07:00") +
                 "\n- Outro. " + TAG.format(ev="07:10") + "\n")
        depois, add, cor = rl.apply_facts(
            antes, [{"text": "Corrigido.", "replaces": "Velho.", "evidence": ["10:00"]}], "2026-10-20")
        self.assertNotIn("- Velho.", depois)
        self.assertIn("- Corrigido. <!-- lucid: date=2026-10-20 evidence=10:00 said_by=unknown source=voice -->", depois)
        self.assertIn("- Outro.", depois)
        self.assertEqual((add, cor), (0, 1))
        # replaces de uma linha FORA da seção: a linha fica intacta e o fato entra como novo
        depois2, add2, cor2 = rl.apply_facts(
            antes, [{"text": "Troca.", "replaces": "Fora da secao.", "evidence": []}], "2026-10-20")
        self.assertIn("- Fora da secao.\n", depois2)
        self.assertIn("- Troca. <!-- lucid:", depois2)
        self.assertEqual((add2, cor2), (1, 0))

    def test_texto_limpo(self):
        novo, _, _ = rl.apply_facts("a\n", [{"text": " linha1\nlinha2 <!-- x --> ", "replaces": None,
                                             "evidence": ["09:00 --> <!--"]}], "2026-10-20")
        corpo = novo.split("\n## Aprendido nas conversas de voz\n")[1]
        self.assertEqual(corpo.count("<!--"), 1)
        self.assertEqual(corpo.count("-->"), 1)
        self.assertIn("linha1 linha2 x", corpo)


class TestDiretrizes(unittest.TestCase):
    def test_diretriz_nova_entra_no_fim_de_directives(self):
        novo, n = rl.apply_directives(
            USER_MD, [{"text": "Prefer short answers.", "replaces": None, "evidence": ["09:00"]}], "2026-10-20")
        bloco = ("<!-- observed: 2026-10-20 | status: active -->\n\n- Prefer short answers. "
                 "<!-- lucid: date=2026-10-20 evidence=09:00 said_by=unknown source=voice -->\n")
        self.assertIn(bloco, novo)
        self.assertLess(novo.index(bloco), novo.index("## Related"))
        self.assertGreater(novo.index(bloco), novo.index("- Always show time in UTC."))
        self.assertEqual(n, 1)
        # o bloco de exemplo do cabeçalho não muda
        self.assertTrue(novo.startswith(USER_MD.split("## Directives")[0]))

    def test_diretriz_que_substitui(self):
        novo, n = rl.apply_directives(
            USER_MD, [{"text": "Always resolve requests by room precedence.",
                       "replaces": "Always resolve device requests prioritizing the current room.",
                       "evidence": ["10:00"]}], "2026-10-20")
        self.assertIn("<!-- observed: 2026-10-08 | status: superseded -->\n\n- Always resolve device requests"
                      " prioritizing the current room.\n\n<!-- observed: 2026-10-20 | status: active -->\n\n"
                      "- Always resolve requests by room precedence. <!-- lucid:", novo)
        self.assertEqual(n, 1)

    def test_diretriz_com_replaces_que_nao_casa_e_descartada(self):
        novo, n = rl.apply_directives(
            USER_MD, [{"text": "Prefer X.", "replaces": "texto que não existe", "evidence": []}], "2026-10-20")
        self.assertEqual((novo, n), (USER_MD, 0))

    def test_replaces_so_casa_com_ativa(self):
        novo, n = rl.apply_directives(
            USER_MD, [{"text": "Prefer X.", "replaces": "Always show time in UTC.", "evidence": []}], "2026-10-20")
        self.assertEqual((novo, n), (USER_MD, 0))

    def test_diretriz_igual_a_ativa_e_ignorada(self):
        novo, n = rl.apply_directives(
            USER_MD, [{"text": "Always address the user as Alex.", "replaces": None, "evidence": []}], "2026-10-20")
        self.assertEqual((novo, n), (USER_MD, 0))

    def test_move_superseded(self):
        novo, blocos = rl.move_superseded(USER_MD)
        self.assertEqual(len(blocos), 2)
        self.assertEqual(blocos[0], "<!-- observed: 2026-09-29 | status: superseded -->\n\n"
                                    "- Prefer turning on the office computer automatically.\n")
        self.assertNotIn("superseded", novo.split("## Directives")[1])
        self.assertIn("- Always address the user as Alex.", novo)
        self.assertIn("- Always resolve device requests prioritizing the current room.", novo)
        self.assertTrue(novo.startswith(USER_MD.split("## Directives")[0]))  # exemplo do cabeçalho intacto
        self.assertIn("status: active -->\n\n- Prefer concise progress updates", novo)
        self.assertIn("## Related", novo)
        self.assertNotIn("\n\n\n\n", novo)

    def test_move_superseded_sem_nenhum(self):
        ativo = USER_MD.replace("superseded", "active")
        novo, blocos = rl.move_superseded(ativo)
        self.assertEqual((novo, blocos), (ativo, []))


class TestCheckAndRestore(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        tmp = Path(self._tmp.name)
        self.fri, self.bk = tmp / "fri", tmp / "bk"
        self.fri.mkdir()
        self.bk.mkdir()
        self.mem = "# Memória\nlinha um\nlinha dois\n"
        self.user = "# Diretrizes\nregra\n"
        for d in (self.fri, self.bk):
            (d / "MEMORY.md").write_text(self.mem, encoding="utf-8")
            (d / "USER.md").write_text(self.user, encoding="utf-8")

    def test_restaura_quando_some_linha(self):
        (self.fri / "MEMORY.md").write_text("# Memória\nlinha um\n", encoding="utf-8")
        (self.fri / "USER.md").write_text("alterado\n", encoding="utf-8")
        self.assertEqual(rl.check_and_restore(self.fri, self.bk), "restored_memory_loss")
        self.assertEqual((self.fri / "MEMORY.md").read_text(encoding="utf-8"), self.mem)
        self.assertEqual((self.fri / "USER.md").read_text(encoding="utf-8"), self.user)

    def test_restaura_quando_user_md_passa_de_3800(self):
        (self.fri / "MEMORY.md").write_text(self.mem + "- novo <!-- lucid: date=2026-10-19 -->\n", encoding="utf-8")
        (self.fri / "USER.md").write_text("x" * 3801, encoding="utf-8")
        self.assertEqual(rl.check_and_restore(self.fri, self.bk), "restored_user_md_over_cap")
        self.assertEqual((self.fri / "MEMORY.md").read_text(encoding="utf-8"), self.mem)
        self.assertEqual((self.fri / "USER.md").read_text(encoding="utf-8"), self.user)

    def test_user_md_ja_acima_do_teto_sem_crescer_nao_restaura(self):
        # 11/11: o cérebro passou o USER.md do teto sozinho; o Lucid não aumentou nada e não pode prender o dia
        (self.bk / "USER.md").write_text("x" * 3845, encoding="utf-8")
        (self.fri / "USER.md").write_text("y" * 3845, encoding="utf-8")
        self.assertEqual(rl.check_and_restore(self.fri, self.bk), "ok")
        self.assertEqual((self.fri / "USER.md").read_text(encoding="utf-8"), "y" * 3845)

    def test_user_md_acima_do_teto_e_maior_que_o_backup_restaura(self):
        (self.bk / "USER.md").write_text("x" * 3845, encoding="utf-8")
        (self.fri / "USER.md").write_text("y" * 3846, encoding="utf-8")
        self.assertEqual(rl.check_and_restore(self.fri, self.bk), "restored_user_md_over_cap")
        self.assertEqual((self.fri / "USER.md").read_text(encoding="utf-8"), "x" * 3845)

    def test_tudo_certo_nao_toca(self):
        novo = self.mem + "- fato <!-- lucid: date=2026-10-19 evidence=10:00 said_by=unknown source=voice -->\n"
        (self.fri / "MEMORY.md").write_text(novo, encoding="utf-8")
        (self.fri / "USER.md").write_text("y" * 3800, encoding="utf-8")
        self.assertEqual(rl.check_and_restore(self.fri, self.bk), "ok")
        self.assertEqual((self.fri / "MEMORY.md").read_text(encoding="utf-8"), novo)

    def test_usa_caracteres_nao_bytes(self):
        (self.fri / "USER.md").write_text("é" * 3800, encoding="utf-8")
        self.assertEqual(rl.check_and_restore(self.fri, self.bk), "ok")


FAKE_PREPARE = '''import sys, os
log = os.environ["FAKE_LOG"]
if sys.argv[1] == "--next":
    if not os.path.exists(log + ".next"):
        open(log + ".next", "w").close()
        print("2026-10-20 5")
else:
    open(log, "a").write(" ".join(sys.argv[1:]) + "\\n")
'''


MISPLACED = USER_MD + "\n<!-- observed: 2026-10-23 | status: active -->\n\n- Always X.\n"


class TestNormalize(unittest.TestCase):
    def _dir_lines(self, texto):
        lines = texto.split("\n")
        ini, fim = rl._section(lines, "## Directives")
        return lines[ini:fim]

    def test_diretriz_depois_de_related_volta_para_directives(self):
        r = rl.normalize_user_md(MISPLACED)
        self.assertNotIn("## Related", r)
        self.assertNotIn("[Agent workspace]", r)
        self.assertIn("- Always X.", self._dir_lines(r))

    def test_normalize_idempotente(self):
        um = rl.normalize_user_md(MISPLACED)
        self.assertEqual(rl.normalize_user_md(um), um)

    def test_normalize_sem_mudanca_quando_ja_certo(self):
        limpo = rl.normalize_user_md(USER_MD)
        self.assertEqual(rl.normalize_user_md(limpo), limpo)
        sem_related = USER_MD.split("\n## Related")[0] + "\n"
        self.assertEqual(rl.normalize_user_md(sem_related), sem_related)

    def test_related_com_conteudo_proprio_fica(self):
        t = USER_MD.replace("- [Agent workspace](/concepts/agent-workspace)\n",
                            "Texto livre\n") + "\n<!-- observed: 2026-10-23 | status: active -->\n\n- Always X.\n"
        r = rl.normalize_user_md(t)
        self.assertIn("## Related", r)
        self.assertIn("Texto livre", r)
        self.assertIn("- Always X.", self._dir_lines(r))
        self.assertNotIn("- Always X.", r.split("## Related")[1])

    def test_exemplo_cercado_nao_e_movido(self):
        r = rl.normalize_user_md(USER_MD)
        self.assertEqual(r.count("Prefer concise progress updates"), 1)
        self.assertLess(r.index("Prefer concise"), r.index("## Directives"))

    def test_ordem_e_formato_preservados(self):
        t = MISPLACED + "\n<!-- observed: 2026-10-24 | status: active -->\n\n- Always Y.\n"
        d = self._dir_lines(rl.normalize_user_md(t))
        self.assertLess(d.index("- Always X."), d.index("- Always Y."))
        i = d.index("- Always X.")
        self.assertEqual((d[i - 2], d[i - 1]), ("<!-- observed: 2026-10-23 | status: active -->", ""))


class TestVoiceView(unittest.TestCase):
    def test_aceita_texto_copiado_com_a_tag(self):
        tag = " <!-- lucid: date=2026-10-24 evidence=10:00 said_by=unknown source=voice -->"
        v, _ = rl.voice_directives_view(USER_MD, ["Always address the user as Alex." + tag], [])
        self.assertEqual(v, "- Always address the user as Alex.")

    def test_corta_por_linha_inteira(self):
        textos = [f"Prefer item number {i:02d} " + "x" * 60 + "." for i in range(20)]
        blocos = "".join(f"<!-- observed: 2026-10-24 | status: active -->\n\n- {t}\n\n" for t in textos)
        user = USER_MD.replace("## Directives\n", "## Directives\n\n" + blocos, 1)
        v, exc = rl.voice_directives_view(user, textos, [])
        self.assertTrue(v)
        self.assertLessEqual(len(v), rl.DIRECTIVES_VIEW_MAX)
        self.assertIn(textos[-1], exc)  # o que não coube vai para as excluídas, não some
        for linha in v.split("\n"):
            self.assertIn(linha[2:], textos)

    def test_so_ativas_exatas(self):
        v, exc = rl.voice_directives_view(USER_MD, ["Always address the user as Alex.", "inventada"], [])
        self.assertEqual(v, "- Always address the user as Alex.")
        self.assertEqual(exc, ["Always resolve device requests prioritizing the current room."])

    def test_conflito_vira_linha(self):
        v, _ = rl.voice_directives_view(USER_MD, ["Always address the user as Alex."], ["A e B"])
        self.assertTrue(v.endswith("- Em conflito: A e B"))

    def test_none_quando_chave_ausente(self):
        self.assertIsNone(rl.voice_directives_view(USER_MD, None, []))

    def test_vista_cortada_no_teto(self):
        longa = "Always " + "x" * 1000
        um = USER_MD.replace("Always address the user as Alex.", longa).replace(
            "Always resolve device requests prioritizing the current room.", "Always " + "y" * 1000)
        v, _ = rl.voice_directives_view(um, [longa, "Always " + "y" * 1000], [])
        self.assertLessEqual(len(v), rl.DIRECTIVES_VIEW_MAX)


class TestParseNovas(unittest.TestCase):
    BASE = {"facts": [], "directives": [], "voice_briefing": "", "brain_capabilities": "", "discarded": []}

    def test_parse_result_aceita_sem_as_chaves_novas(self):
        r = rl.parse_result(json.dumps(self.BASE))
        self.assertIsNone(r["voice_directives"])
        self.assertEqual(r["voice_directive_conflicts"], [])

    def test_parse_result_aceita_com_as_chaves(self):
        r = rl.parse_result(json.dumps(dict(self.BASE, voice_directives=["a"], voice_directive_conflicts=["b"])))
        self.assertEqual((r["voice_directives"], r["voice_directive_conflicts"]), (["a"], ["b"]))

    def test_parse_result_ignora_tipo_errado(self):
        r = rl.parse_result(json.dumps(dict(self.BASE, voice_directives="x", voice_directive_conflicts=3)))
        self.assertIsNone(r["voice_directives"])
        self.assertEqual(r["voice_directive_conflicts"], [])


class TestRecordUsage(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._old = os.environ.get("FRIDAY_COST_STATE")
        self.addCleanup(self._restore)

    def _restore(self):
        if self._old is None:
            os.environ.pop("FRIDAY_COST_STATE", None)
        else:
            os.environ["FRIDAY_COST_STATE"] = self._old

    def test_record_usage_appends_lucid_line(self):
        os.environ["FRIDAY_COST_STATE"] = self._tmp.name
        rl.record_usage({"promptTokenCount": 1000, "cachedContentTokenCount": 400, "candidatesTokenCount": 50,
                         "thoughtsTokenCount": 70}, "2026-10-23", "2026-10-24T00:30:00")
        files = list((Path(self._tmp.name) / "usage").glob("*.ndjson"))
        self.assertEqual(len(files), 1)
        r = json.loads(files[0].read_text().splitlines()[0])
        self.assertEqual((r["source"], r["origin"], r["model"], r["session"]),
                         ("lucid", "lucid", rl.MODEL, "lucid:2026-10-23"))
        self.assertEqual(r["id"], "lucid:2026-10-23:2026-10-24T00:30:00")
        t = r["tokens"]
        self.assertEqual((t["input_text"], t["cache_read_text"], t["output_text"], t["reasoning"]), (600, 400, 50, 70))

    def test_record_usage_never_raises(self):
        blocker = Path(self._tmp.name) / "blocker"
        blocker.write_text("x")
        os.environ["FRIDAY_COST_STATE"] = str(blocker)
        rl.record_usage({"promptTokenCount": 1}, "2026-10-23", "t")


class TestMain(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.fri, self.cat, self.nd = self.tmp / "fri", self.tmp / "cat", self.tmp / "nd"
        for d in (self.fri, self.nd, self.cat / "inbox", self.cat / "out", self.cat / "state",
                  self.cat / "backups" / "20261020"):
            d.mkdir(parents=True)
        self.mem = "# Memória\nlinha um\n"
        self.user = USER_MD
        (self.fri / "MEMORY.md").write_text(self.mem, encoding="utf-8")
        (self.fri / "USER.md").write_text(self.user, encoding="utf-8")
        for n, t in (("MEMORY.md", self.mem), ("USER.md", self.user)):
            (self.cat / "backups" / "20261020" / n).write_text(t, encoding="utf-8")
        (self.cat / "inbox" / "voice-day.md").write_text("# Conversas\n\n08:10 x\n", encoding="utf-8")
        (self.cat / "inbox" / "brain-skills.md").write_text("- weather: Current weather\n", encoding="utf-8")
        self.key = self.tmp / "key"
        self.key.write_text("SEGREDO\n", encoding="utf-8")
        self.fake = self.tmp / "fake_prepare.py"
        self.fake.write_text(FAKE_PREPARE, encoding="utf-8")
        self.log = self.tmp / "prep.log"
        self.env = {"NDJSON_DIR": str(self.nd), "CATALOGER_WS": str(self.cat), "FRIDAY_WS": str(self.fri),
                    "GEMINI_KEY_FILE": str(self.key), "PREPARE_DAY": str(self.fake), "FAKE_LOG": str(self.log),
                    "TODAY": "2026-10-21", "FRIDAY_COST_STATE": str(self.tmp / "cost")}
        self._old = {k: os.environ.get(k) for k in self.env}
        os.environ.update(self.env)
        self.addCleanup(self._restore_env)
        self._call = rl.call_gemini
        self.addCleanup(setattr, rl, "call_gemini", self._call)

    def _restore_env(self):
        for k, v in self._old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def _gemini(self, texto, usage=None):
        self.calls = []

        def falso(body, key, **kw):
            self.calls.append((body, key))
            return {"candidates": [{"content": {"parts": [{"text": texto}]}}],
                    "usageMetadata": usage or {"promptTokenCount": 10000, "candidatesTokenCount": 500,
                                               "thoughtsTokenCount": 500}}
        rl.call_gemini = falso

    def _runs(self):
        p = self.cat / "state" / "runs.ndjson"
        return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines()] if p.exists() else []

    def _done(self):
        return self.log.read_text(encoding="utf-8").split("\n") if self.log.exists() else []

    def _result(self, **kw):
        base = {"facts": [{"text": "A sala tem dois Echos.", "replaces": None, "evidence": ["08:10"]}],
                "directives": [], "voice_briefing": "- A sala tem dois Echos.",
                "brain_capabilities": "O cérebro consegue pesquisar.", "discarded": [{"text": "x", "reason": "y"}]}
        base.update(kw)
        return json.dumps(base)

    def test_main_grava_e_marca_feito(self):
        self._gemini(self._result())
        rl.main(["--max-days", "1"])
        mem = (self.fri / "MEMORY.md").read_text(encoding="utf-8")
        self.assertIn("- A sala tem dois Echos. <!-- lucid: date=2026-10-20 evidence=08:10", mem)
        self.assertEqual((self.cat / "out" / "voice-briefing.md").read_text(encoding="utf-8"), "- A sala tem dois Echos.")
        self.assertTrue((self.cat / "out" / "brain-capabilities.md").read_text(encoding="utf-8").startswith("O cérebro"))
        runs = self._runs()
        self.assertEqual(len(runs), 1)
        self.assertEqual((runs[0]["status"], runs[0]["facts_added"], runs[0]["turns_in"], runs[0]["discarded"]),
                         ("ok", 1, 5, 1))
        self.assertEqual(runs[0]["discarded_items"], [{"text": "x", "reason": "y"}])
        self.assertEqual(runs[0]["date"], "2026-10-20")
        self.assertAlmostEqual(runs[0]["cost_usd"], (10000 * 0.75 + 1000 * 3.75) / 1e6, places=6)
        self.assertIn("--done 2026-10-20", self._done())
        self.assertEqual(self.calls[0][1], "SEGREDO")
        # o prompt de sistema e a memória foram para a chamada
        corpo = json.dumps(self.calls[0][0], ensure_ascii=False)
        self.assertIn("Lucid Dreaming", corpo)
        self.assertIn("linha um", corpo)

    def test_main_grava_vista_de_diretrizes(self):
        self._gemini(self._result(voice_directives=["Always address the user as Alex.", "inventada"],
                                  voice_directive_conflicts=["A e B"]))
        rl.main(["--max-days", "1"])
        vista = (self.cat / "out" / "voice-directives.md").read_text(encoding="utf-8")
        self.assertEqual(vista, "- Always address the user as Alex.\n- Em conflito: A e B")
        exc = json.loads((self.cat / "out" / "voice-directives-excluded.json").read_text(encoding="utf-8"))
        self.assertEqual(exc["excluded"], ["Always resolve device requests prioritizing the current room."])
        self.assertIn("generated_at", exc)

    def test_main_sem_chave_nao_toca_vista(self):
        (self.cat / "out" / "voice-directives.md").write_text("- antiga", encoding="utf-8")
        self._gemini(self._result())
        rl.main(["--max-days", "1"])
        self.assertEqual((self.cat / "out" / "voice-directives.md").read_text(encoding="utf-8"), "- antiga")
        self.assertFalse((self.cat / "out" / "voice-directives-excluded.json").exists())

    def test_main_normaliza_user_md_com_diretriz_depois_de_related(self):
        (self.fri / "USER.md").write_text(MISPLACED, encoding="utf-8")
        self._gemini(self._result())
        rl.main(["--max-days", "1"])
        user = (self.fri / "USER.md").read_text(encoding="utf-8")
        self.assertNotIn("## Related", user)
        self.assertIn("- Always X.", user.split("## Directives")[1])

    def test_main_json_ruim_nao_grava(self):
        self._gemini("oops")
        rl.main(["--max-days", "3"])
        self.assertEqual((self.fri / "MEMORY.md").read_text(encoding="utf-8"), self.mem)
        self.assertEqual((self.fri / "USER.md").read_text(encoding="utf-8"), self.user)
        self.assertEqual(list((self.cat / "out").iterdir()), [])
        runs = self._runs()
        self.assertEqual([r["status"] for r in runs], ["run_failed"])
        self.assertGreater(runs[0]["cost_usd"], 0)  # a resposta existiu: o custo é registrado
        self.assertNotIn("--done 2026-10-20", self._done())

    def test_main_erro_de_rede_nao_grava(self):
        def quebra(body, key, **kw):
            raise rl.LucidCallError("HTTP 503: high demand")
        rl.call_gemini = quebra
        rl.main(["--max-days", "1"])
        self.assertEqual([r["status"] for r in self._runs()], ["run_failed"])
        self.assertEqual((self.fri / "MEMORY.md").read_text(encoding="utf-8"), self.mem)
        self.assertNotIn("--done 2026-10-20", self._done())

    def test_main_user_md_cheio(self):
        base = USER_MD.replace("status: superseded", "status: active")
        pad = 3790 - len(base)
        user = base.replace("## Related", "- " + "x" * (pad - 4) + "\n\n## Related")
        self.assertEqual(len(user), 3790)
        (self.fri / "USER.md").write_text(user, encoding="utf-8")
        (self.cat / "backups" / "20261020" / "USER.md").write_text(user, encoding="utf-8")
        self._gemini(self._result(directives=[{"text": "Prefer long explanations when asked.", "replaces": None,
                                               "evidence": ["09:00"]}]))
        rl.main(["--max-days", "1"])
        # sem as diretrizes novas; o rodapé `## Related` (só links) sai pela normalização
        self.assertEqual((self.fri / "USER.md").read_text(encoding="utf-8"), rl.normalize_user_md(user))
        runs = self._runs()
        self.assertEqual(runs[0]["status"], "user_md_full")
        self.assertEqual(runs[0]["directives_changed"], 0)
        self.assertIn("A sala tem dois Echos.", (self.fri / "MEMORY.md").read_text(encoding="utf-8"))
        self.assertTrue((self.cat / "out" / "voice-briefing.md").exists())
        self.assertIn("--done 2026-10-20", self._done())

    def test_main_user_md_ja_acima_do_teto_termina_o_dia(self):
        # 11/11: o dia 09/11 voltava toda noite como restored_user_md_over_cap
        base = USER_MD.replace("status: superseded", "status: active")
        user = base.replace("## Related", "- " + "x" * (3990 - len(base) - 4) + "\n\n## Related")
        self.assertGreater(len(rl.normalize_user_md(user)), rl.USER_MD_CAP)
        (self.fri / "USER.md").write_text(user, encoding="utf-8")
        (self.cat / "backups" / "20261020" / "USER.md").write_text(user, encoding="utf-8")
        self._gemini(self._result(directives=[{"text": "Prefer long explanations when asked.", "replaces": None,
                                               "evidence": ["09:00"]}]))
        rl.main(["--max-days", "1"])
        runs = self._runs()
        self.assertEqual(runs[0]["status"], "user_md_full")
        self.assertEqual(runs[0]["directives_changed"], 0)
        self.assertIn("A sala tem dois Echos.", (self.fri / "MEMORY.md").read_text(encoding="utf-8"))
        self.assertIn("--done 2026-10-20", self._done())

    def test_main_move_superseded_e_grava_diretriz(self):
        self._gemini(self._result(directives=[{"text": "Prefer short answers.", "replaces": None,
                                               "evidence": ["09:00"]}]))
        rl.main(["--max-days", "1"])
        user = (self.fri / "USER.md").read_text(encoding="utf-8")
        self.assertNotIn("superseded", user.split("## Directives")[1])
        self.assertIn("- Prefer short answers. <!-- lucid:", user)
        mv = (self.fri / "memory" / "user-superseded.md").read_text(encoding="utf-8")
        self.assertTrue(mv.startswith("# Diretrizes superseded do USER.md (movidas pelo Lucid Dreaming)\n\n"))
        self.assertIn("<!-- moved: 2026-10-21 -->", mv)
        self.assertIn("- Always show time in UTC.", mv)
        self.assertEqual(mv.count("<!-- moved:"), 2)
        r = self._runs()[0]
        self.assertEqual((r["status"], r["directives_changed"]), ("ok", 1))

    def test_main_replaces_fora_da_secao_nao_apaga_nada(self):
        # o modelo pede para "corrigir" uma linha que não é do Lucid: o fato entra como novo
        self._gemini(self._result(facts=[{"text": "Troca.", "replaces": "linha um", "evidence": []}]))
        rl.main(["--max-days", "1"])
        mem = (self.fri / "MEMORY.md").read_text(encoding="utf-8")
        self.assertIn("linha um\n", mem)
        self.assertEqual(self._runs()[0]["status"], "ok")

    def test_main_guarda_final_restaura_se_algo_sumir(self):
        # simula um bug: apply_facts apagando uma linha que não é do Lucid
        original = rl.apply_facts
        self.addCleanup(setattr, rl, "apply_facts", original)
        rl.apply_facts = lambda mem, facts, day: ("# Memória\n", 1, 0)
        self._gemini(self._result())
        rl.main(["--max-days", "3"])
        self.assertEqual((self.fri / "MEMORY.md").read_text(encoding="utf-8"), self.mem)
        self.assertEqual([r["status"] for r in self._runs()], ["restored_memory_loss"])
        self.assertNotIn("--done 2026-10-20", self._done())

    def test_main_sem_chave_nao_chama(self):
        self.key.unlink()
        self._gemini(self._result())
        rl.main(["--max-days", "1"])
        self.assertEqual(self.calls, [])
        self.assertEqual([r["status"] for r in self._runs()], ["run_failed"])

    def test_sem_turnos_grava_no_turns(self):
        self.fake.write_text("import sys\nsys.exit(0)\n", encoding="utf-8")
        self._gemini(self._result())
        rl.main(["--max-days", "1"])
        self.assertEqual([(r["status"], r["date"]) for r in self._runs()], [("no_turns", "2026-10-20")])
        self.assertEqual(self.calls, [])

    def test_samba_fora_grava_skipped_samba(self):
        self.nd.rmdir()
        self.fake.write_text("import sys\nsys.exit(0)\n", encoding="utf-8")
        rl.main(["--max-days", "1"])
        self.assertEqual([r["status"] for r in self._runs()], ["skipped_samba"])

    def test_stdout_sem_conteudo_nem_chave(self):
        self._gemini(self._result())
        buf = io.StringIO()
        import contextlib
        with contextlib.redirect_stdout(buf):
            rl.main(["--max-days", "1"])
        out = buf.getvalue()
        self.assertEqual(len(out.strip().splitlines()), 1)
        self.assertTrue(out.startswith("2026-10-20 ok "))
        self.assertNotIn("SEGREDO", out)
        self.assertNotIn("sala", out)


class TestReadModel(unittest.TestCase):
    def test_model_from_state_file_with_default(self):
        import json, tempfile, os
        with tempfile.TemporaryDirectory() as d:
            f = os.path.join(d, "models.json")
            self.assertEqual(rl.read_model(f), rl.DEFAULT_MODEL)  # missing
            for bad in ("{", json.dumps({"lucid": "../x"}), json.dumps({"lucid": 3}), json.dumps([1])):
                open(f, "w").write(bad)
                self.assertEqual(rl.read_model(f), rl.DEFAULT_MODEL)
            open(f, "w").write(json.dumps({"lucid": "gemini-3.8-pro"}))
            self.assertEqual(rl.read_model(f), "gemini-3.8-pro")


if __name__ == "__main__":
    unittest.main()
