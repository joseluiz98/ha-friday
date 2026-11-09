"""cd /srv/friday/app && python3 -m unittest test_voice_skills -v"""
import json, os, tempfile, unittest
import voice_skills as vs

INV_TEXT = "Static Context:\n- names: Luz Quarto, Luz do quarto\n  domain: light\n  areas: Bedroom, Quarto\n"
SCHEMAS = {
    "intent__HassTurnOn": {"properties": {"name": {"type": "string"}, "area": {"type": "string"},
                                          "domain": {"type": "array"}}, "required": []},
    "criar_timer": {"properties": {"duracao_s": {"type": "integer"}, "rotulo": {"type": "string"}},
                    "required": ["duracao_s"]},
    "perguntar_ao_cerebro": {"properties": {"pedido": {"type": "string"}}, "required": ["pedido"]},
}
INV = vs.parse_inventory(INV_TEXT)


def boa(**kw):
    c = {"name": "Modo Leitura", "description": "Acende a luz do quarto.", "teaching": "direct",
         "taught_by": "Alex", "ordered": False, "original_request": "modo leitura é luz do quarto",
         "steps": [{"tool": "intent__HassTurnOn", "args": {"area": "Quarto", "domain": ["light"]}}]}
    c.update(kw)
    return c


class TestArquivos(unittest.TestCase):
    def test_load_ausente_e_corrompido(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(vs.load(os.path.join(d, "x.json")), [])
            p = os.path.join(d, "r.json")
            with open(p, "w") as f:
                f.write("{nao json")
            self.assertEqual(vs.load(p), [])
            with open(p, "w") as f:
                f.write('{"skills": "x"}')
            self.assertEqual(vs.load(p), [])

    def test_save_atomico_e_rele(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "sub", "skills.json")
            sk = [{"id": "kabc123", "name": "Á"}]
            vs.save(sk, p)
            self.assertEqual(vs.load(p), sk)
            self.assertEqual(os.listdir(os.path.dirname(p)), ["skills.json"])

    def test_read_config_padrao_desligado(self):
        self.assertEqual(vs.read_config("/nao/existe.json"), {"notify_new_skill": False})
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "c.json")
            with open(p, "w") as f:
                f.write('{"notify_new_skill": true}')
            self.assertEqual(vs.read_config(p), {"notify_new_skill": True})
            with open(p, "w") as f:
                f.write("ruim")
            self.assertEqual(vs.read_config(p), {"notify_new_skill": False})


class TestInventario(unittest.TestCase):
    def test_parse_inventory(self):
        self.assertEqual(INV.names, {"luz quarto", "luz do quarto"})
        self.assertEqual(INV.areas, {"bedroom", "quarto"})
        self.assertEqual(INV.domains, {"light"})
        self.assertEqual(INV.area_labels, {"Bedroom", "Quarto"})


class TestValidate(unittest.TestCase):
    def test_valida_receita_boa(self):
        self.assertEqual(vs.validate(boa(), SCHEMAS, INV), [])

    def test_area_inexistente(self):
        e = vs.validate(boa(steps=[{"tool": "intent__HassTurnOn", "args": {"area": "Varanda"}}]), SCHEMAS, INV)
        self.assertEqual(e, ["passo 1: área 'Varanda' não existe na casa"])

    def test_nome_e_dominio_inexistentes(self):
        e = vs.validate(boa(steps=[{"tool": "intent__HassTurnOn", "args": {"name": "X", "domain": ["fan"]}}]),
                        SCHEMAS, INV)
        self.assertEqual(len(e), 2)
        self.assertTrue(all(x.startswith("passo 1:") for x in e))

    def test_ferramenta_inexistente(self):
        e = vs.validate(boa(steps=[{"tool": "voar", "args": {}}]), SCHEMAS, INV)
        self.assertTrue(e[0].startswith("passo 1:") and "voar" in e[0])

    def test_ferramenta_proibida(self):
        e = vs.validate(boa(steps=[{"tool": "perguntar_ao_cerebro", "args": {"pedido": "x"}}]), SCHEMAS, INV)
        self.assertTrue(e[0].startswith("passo 1:") and "perguntar_ao_cerebro" in e[0])

    def test_falta_argumento_obrigatorio(self):
        e = vs.validate(boa(steps=[{"tool": "criar_timer", "args": {}}]), SCHEMAS, INV)
        self.assertEqual(len(e), 1)
        self.assertIn("duracao_s", e[0])

    def test_argumento_desconhecido(self):
        e = vs.validate(boa(steps=[{"tool": "criar_timer", "args": {"duracao_s": 5, "x": 1}}]), SCHEMAS, INV)
        self.assertEqual(len(e), 1)
        self.assertIn("'x'", e[0])

    def test_tipo_errado(self):
        e = vs.validate(boa(steps=[{"tool": "criar_timer", "args": {"duracao_s": "5"}}]), SCHEMAS, INV)
        self.assertEqual(len(e), 1)
        self.assertTrue(e[0].startswith("passo 1:") and "integer" in e[0])

    def test_nativa_sem_checar_alvo(self):
        self.assertEqual(vs.validate(boa(steps=[{"tool": "criar_timer", "args": {"duracao_s": 5, "rotulo": "Varanda"}}]),
                                     SCHEMAS, INV), [])

    def test_teaching_invalido(self):
        e = vs.validate(boa(teaching="magia"), SCHEMAS, INV)
        self.assertTrue(any("teaching" in x for x in e))

    def test_campos_basicos(self):
        self.assertTrue(vs.validate(boa(name=""), SCHEMAS, INV))
        self.assertTrue(vs.validate(boa(description="x" * 201), SCHEMAS, INV))
        self.assertTrue(vs.validate(boa(steps=[]), SCHEMAS, INV))
        self.assertTrue(vs.validate(boa(steps=[{"tool": "criar_timer", "args": {"duracao_s": 1}}] * 21), SCHEMAS, INV))
        self.assertEqual(vs.validate(boa(taught_by=""), SCHEMAS, INV), [])


NOW = "2026-10-23T20:00:00-03:00"


class TestRegistro(unittest.TestCase):
    def test_upsert_cria(self):
        sk, r = vs.upsert([], boa(), NOW)
        self.assertTrue(r["ok"] and r["created"])
        s = r["skill"]
        self.assertRegex(s["id"], r"^k[0-9a-f]{6}$")
        self.assertEqual((s["use_count"], s["last_used"], s["taught_at"], s["taught_by"]), (0, None, NOW, "Alex"))
        self.assertEqual(sk, [s])
        self.assertNotIn("replace_id", s)

    def test_upsert_taught_by_vazio_vira_unknown(self):
        _, r = vs.upsert([], boa(taught_by=""), NOW)
        self.assertEqual(r["skill"]["taught_by"], "unknown")

    def test_upsert_mesmo_autor_atualiza_mantem_id_e_uso(self):
        sk, r = vs.upsert([], boa(), NOW)
        sk = vs.mark_used(sk, r["skill"]["id"], NOW)
        sk2, r2 = vs.upsert(sk, boa(name="modo leitura", description="Nova."), "2026-10-24T10:00:00-03:00")
        self.assertTrue(r2["ok"] and not r2["created"])
        self.assertEqual(len(sk2), 1)
        self.assertEqual((sk2[0]["id"], sk2[0]["use_count"], sk2[0]["description"], sk2[0]["taught_at"]),
                         (r["skill"]["id"], 1, "Nova.", "2026-10-24T10:00:00-03:00"))

    def test_upsert_outro_autor_conflito(self):
        sk, r = vs.upsert([], boa(), NOW)
        sk2, r2 = vs.upsert(sk, boa(taught_by="Bia"), NOW)
        self.assertFalse(r2["ok"])
        self.assertEqual(r2["conflict"]["id"], r["skill"]["id"])
        self.assertEqual(len(sk2), 1)
        self.assertEqual(sk2[0]["taught_by"], "Alex")

    def test_upsert_replace_id_substitui(self):
        sk, r = vs.upsert([], boa(), NOW)
        sk2, r2 = vs.upsert(sk, boa(taught_by="Bia", description="Da Bia.", replace_id=r["skill"]["id"]), NOW)
        self.assertTrue(r2["ok"])
        self.assertEqual((sk2[0]["id"], sk2[0]["taught_by"], len(sk2)), (r["skill"]["id"], "Bia", 1))

    def test_delete_por_id_e_por_nome(self):
        sk, r = vs.upsert([], boa(), NOW)
        sk, _ = vs.upsert(sk, boa(name="Outra"), NOW)
        a, rem = vs.delete(sk, r["skill"]["id"])
        self.assertEqual((len(a), rem["name"]), (1, "Modo Leitura"))
        b, rem = vs.delete(sk, "outra")
        self.assertEqual((len(b), rem["name"]), (1, "Outra"))
        c, rem = vs.delete(sk, "nada")
        self.assertEqual((len(c), rem), (2, None))


def muitas(n):
    sk = []
    for i in range(n):
        sk, _ = vs.upsert(sk, boa(name=f"Habilidade número {i}", description="Faz uma coisa bem específica."), NOW)
    return sk


class TestCatalogo(unittest.TestCase):
    def test_catalogo_vazio(self):
        self.assertEqual(vs.catalog([]), ("", False))

    def test_catalogo_cabe(self):
        sk, r = vs.upsert([], boa(), NOW)
        t, corte = vs.catalog(sk)
        self.assertFalse(corte)
        self.assertTrue(t.startswith(vs.CATALOG_HEADER))
        self.assertIn(f"- Modo Leitura (id {r['skill']['id']}): Acende a luz do quarto.; ensinada por Alex", t)

    def test_catalogo_unknown_sem_ensinada_por(self):
        sk, _ = vs.upsert([], boa(taught_by="unknown"), NOW)
        self.assertNotIn("ensinada por", vs.catalog(sk)[0])

    def test_catalogo_corta_e_avisa(self):
        t, corte = vs.catalog(muitas(40))
        self.assertTrue(corte)
        self.assertLessEqual(len(t), vs.CATALOG_MAX)
        self.assertRegex(t.splitlines()[-1], r"^- Há mais \d+ habilidades guardadas, que a ferramenta search_skills encontra por assunto\.$")

    def test_catalogo_ordem_por_uso(self):
        sk = muitas(3)
        sk = vs.mark_used(sk, sk[2]["id"], NOW)
        t, _ = vs.catalog(sk)
        self.assertEqual(t.splitlines()[1].split(" (id")[0], "- Habilidade número 2")

    def test_catalogo_sem_ordens(self):
        t, _ = vs.catalog(muitas(40))
        for palavra in ("use ", "chame", "sempre"):
            self.assertNotIn(palavra, t)


class TestBusca(unittest.TestCase):
    def test_search_acha_por_assunto_sem_acento(self):
        sk, _ = vs.upsert([], boa(), NOW)
        self.assertEqual([x["name"] for x in vs.search(sk, "leitura")], ["Modo Leitura"])
        self.assertEqual([x["name"] for x in vs.search(sk, "LÊITURA")], ["Modo Leitura"])
        self.assertEqual(vs.search(sk, "cinema"), [])
        sk2, _ = vs.upsert([], boa(name="Modo Concentração", description="Luz."), NOW)
        self.assertEqual(len(vs.search(sk2, "concentracao")), 1)


class TestExecucao(unittest.TestCase):
    def test_call_succeeded(self):
        ok_ha = ('{"speech": {}, "response_type": "action_done", "data": {"success": [{"name": "Office", '
                 '"type": "area", "id": "jose_s_office"}], "failed": []}}')
        falha_ha = ('{"speech": {}, "response_type": "action_done", "data": {"success": [], '
                    '"failed": [{"name": "X"}]}}')
        casos = [({"ok": False}, False), ({"erro": "x"}, False), ({"error": "x"}, False), ({"ok": True}, True),
                 ("Error calling mcp tool x", False), ("Sorry, could not call the mcp tool", False), (falha_ha, False), (ok_ha, True),
                 ('{"response_type": "error"}', False), ("texto qualquer", True),
                 # 08/11: o MCP do HA embrulha a resposta do script em "result" (music_play com ok=false entrou
                 # como "deu certo" e o cérebro afirmou que a música tocava)
                 ('{"result": {"ok": false, "mode": "play", "players": ["media_player.x"]}}', False),
                 ('{"result": {"ok": true, "mode": "play"}}', True), ({"result": {"ok": False}}, False),
                 ('{"result": {"error": "x"}}', False), ('{"result": "texto"}', True)]
        for entrada, esperado in casos:
            self.assertEqual(vs.call_succeeded(entrada), esperado, entrada)

    def test_record_call_uma_vez_por_id(self):
        # 08/11: o mesmo FunctionCallResultFrame passou duas vezes pelo observer e a chamada saiu dobrada
        calls, vistos = [], set()
        for _ in range(2):
            vs.record_call(calls, vistos, "c1", "script__music_play", {"query": "flow"}, '{"result": {"ok": false}}')
        vs.record_call(calls, vistos, "c2", "intent__HassTurnOn", {"name": "Luz"}, "texto", error=None)
        vs.record_call(calls, vistos, "c3", "x", None, {"ok": True}, error="boom")
        self.assertEqual(calls, [{"tool": "script__music_play", "args": {"query": "flow"}, "ok": False},
                                 {"tool": "intent__HassTurnOn", "args": {"name": "Luz"}, "ok": True},
                                 {"tool": "x", "args": {}, "ok": False}])
        vs.record_call(calls, vistos, None, "y", {}, "texto")  # sem id: anota sempre
        self.assertEqual(len(calls), 4)

    def test_demonstration_block(self):
        calls = [{"tool": "a", "args": {"x": 1}, "ok": True}, {"tool": "b", "args": {}, "ok": False},
                 {"tool": "perguntar_ao_cerebro", "args": {}, "ok": True}, {"tool": "c", "args": {"y": "é"}, "ok": True}]
        self.assertEqual(vs.demonstration_block(calls),
                         '\n\n[chamadas de ferramenta que deram certo nesta conversa, em ordem: a {"x": 1}; c {"y": "é"}]')

    def test_demonstration_block_corte_mantem_as_ultimas(self):
        calls = [{"tool": f"t{i}", "args": {"v": "z" * 300}, "ok": True} for i in range(10)]
        b = vs.demonstration_block(calls)
        self.assertLessEqual(len(b), vs.DEMO_MAX_CHARS)
        self.assertIn("t9", b)
        self.assertNotIn("t0 ", b)

    def test_demonstration_block_vazio(self):
        self.assertEqual(vs.demonstration_block([]), "")
        self.assertEqual(vs.demonstration_block([{"tool": "a", "args": {}, "ok": False}]), "")

    def test_run_results_marca_not_run(self):
        sk, r = vs.upsert([], boa(steps=[{"tool": "intent__HassTurnOn", "args": {"area": "Quarto"}},
                                         {"tool": "criar_timer", "args": {"duracao_s": 5}},
                                         {"tool": "intent__HassTurnOn", "args": {"area": "Bedroom"}}]), NOW)
        plano = vs.run_plan(r["skill"], {"intent__HassTurnOn"})
        self.assertEqual([p["run"] for p in plano], [True, False, True])
        res = vs.run_results(plano, {0: (True, "ok"), 2: (False, "boom" * 200)}, "Modo Leitura")
        self.assertEqual([s["status"] for s in res["steps"]], ["ok", "not_run", "failed"])
        self.assertEqual(res["steps"][1]["detail"], "ferramenta da própria voz; o run_skill executa só as do Home Assistant")
        self.assertEqual(len(res["steps"][2]["detail"]), 300)
        self.assertEqual(res["skill"], "Modo Leitura")

    def test_tool_schemas(self):
        class T:
            name, properties, required = "x", {"a": {"type": "string"}}, ["a"]
        self.assertEqual(vs.tool_schemas([T()]), {"x": {"properties": {"a": {"type": "string"}}, "required": ["a"]}})


if __name__ == "__main__":
    unittest.main()
