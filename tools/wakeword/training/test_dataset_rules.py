"""cd /homeassistant/tools/satelite/wakeword-training && python3 -m unittest -q test_dataset_rules"""
import unittest
import dataset_rules as r


def W(w, i, f):
    return {"w": " " + w, "i": i, "f": f, "p": 0.9}


def src(name, s, ts=None):
    return {"name": name, "src": s, "ts": ts}


def sc(dur, base=None, verif=None):
    n = int(dur / 0.08)
    return {"dur": dur, "base": base or [0.0] * n, "verif": verif if verif is not None else [], "vad": [0.0] * n}


class TestWake(unittest.TestCase):
    def test_friday_variantes_e_pontuacao(self):
        for w in ("Friday", " friday,", "Freddy.", "FRAIDEI!"):
            self.assertEqual(len(r.wake_spans([W(w, 1, 2)], "friday")), 1, w)
        self.assertEqual(r.wake_spans([W("Fried", 1, 2), W("Monday", 3, 4)], "friday"), [])

    def test_sexta_feira_formas(self):
        self.assertEqual(r.wake_spans([W("Sexta-feira", 1, 2)], "sexta_feira"), [(1, 2, 0)])
        self.assertEqual(r.wake_spans([W("Sexta", 1, 1.5), W("feira.", 1.6, 2.2)], "sexta_feira"), [(1, 2.2, 0)])
        self.assertEqual(r.wake_spans([W("Sexta", 1, 1.5), W("-feira", 1.6, 2.2)], "sexta_feira"), [(1, 2.2, 0)])
        self.assertEqual(r.wake_spans([W("sextafeira", 1, 2)], "sexta_feira"), [(1, 2, 0)])
        self.assertEqual(r.wake_spans([W("Sexta", 1, 1.5), W("passada", 1.6, 2.2)], "sexta_feira"), [])

    def test_has_wake(self):
        ws = [W("oi", 0, 1), W("Friday", 1, 2)]
        self.assertFalse(r.has_wake(ws, 0)); self.assertTrue(r.has_wake(ws, 1))


class TestWindowMax(unittest.TestCase):
    def test_window_max_atraso(self):
        arr = [0.0] * 200
        arr[int(11.0 / 0.08)] = 0.9   # 0,6 s depois do fim da palavra (10,0–10,4): dentro da janela
        self.assertAlmostEqual(r.window_max(arr, 10.0, 10.4), 0.9)
        arr2 = [0.0] * 200; arr2[int(12.5 / 0.08)] = 0.9  # 2,1 s depois: fora
        self.assertEqual(r.window_max(arr2, 10.0, 10.4), 0.0)
        self.assertEqual(r.window_max([], 1.0, 2.0), 0.0)
        self.assertEqual(r.window_max([0.5, 0.2], 100.0, 101.0), 0.0)  # além do fim do array


class TestLabel(unittest.TestCase):
    def test_label_rotulado(self):
        self.assertEqual(r.label("acervo_v", True, 0.0, "friday"), ("pos", "rotulado"))
        self.assertEqual(r.label("sessao", True, 0.0, "sexta_feira"), ("pos", "rotulado"))

    def test_label_verificador(self):
        self.assertEqual(r.label("quase", True, 0.5, "friday"), ("pos", "verificador"))
        self.assertEqual(r.label("contexto", True, 0.9, "friday"), ("pos", "verificador"))

    def test_label_voz_nao_confirmada(self):
        self.assertEqual(r.label("quase", True, 0.49, "friday"), (None, "voz não confirmada"))
        self.assertEqual(r.label("quase", True, 0.99, "sexta_feira"), ("pos", "verificador"))
        self.assertEqual(r.label("quase", True, 0.1, "sexta_feira"), ("pos", "palavra transcrita"))
        self.assertEqual(r.label("barrada", True, 0.1, "friday")[0], None)

    def test_label_neg(self):
        for s in ("barrada", "sem_voz", "acervo_f", "fala_livre", "contexto"):
            self.assertEqual(r.label(s, False, 0.0, "friday"), ("neg", "sem palavra"), s)

    def test_label_sem_palavra_nas_outras(self):
        for s in ("ativou", "quase", "acervo_v", "sessao"):
            self.assertEqual(r.label(s, False, 0.9, "friday"), (None, "sem palavra transcrita"), s)

    def test_label_acervo_f_com_palavra_descartada(self):
        self.assertEqual(r.label("acervo_f", True, 0.9, "friday")[0], None)


class TestItens(unittest.TestCase):
    def test_contexto_pos_e_neg_sem_sobrepor(self):
        ws = [W("oi", 5.0, 5.3), W("Friday", 10.0, 10.4), W("tchau", 9.5, 9.8), W("legal", 11.0, 11.3),
              W("vamos", 20.0, 20.3), W("la", 20.4, 20.6)]
        its = r.items_for(src("contexto_x.wav", "contexto"), ws, sc(30.0), "friday")
        pos = [i for i in its if i["label"] == "pos"]
        neg = [i for i in its if i["label"] == "neg"]
        self.assertEqual(pos, [])  # sem verificador confirmando: a voz não está confirmada
        self.assertEqual([i["why"] for i in its if i["ini"] == 10.0], ["voz não confirmada"])
        self.assertEqual(len(neg), 2)  # "oi" e "vamos la"
        for n in neg:
            self.assertFalse(n["ini"] < 11.4 and n["fim"] > 9.0, n)

    def test_contexto_pos_com_verificador(self):
        verif = [0.0] * 375; verif[int(10.2 / 0.08)] = 0.8
        its = r.items_for(src("c.wav", "contexto"), [W("Friday", 10.0, 10.4)], sc(30.0, verif=verif), "friday")
        self.assertEqual([(i["label"], i["why"]) for i in its], [("pos", "verificador")])

    def test_neg_no_maximo_3s(self):
        ws = [W(f"p{i}", i * 0.4, i * 0.4 + 0.3) for i in range(20)]  # fala contínua de 8 s
        its = r.items_for(src("fl.wav", "fala_livre"), ws, sc(10.0), "friday")
        self.assertTrue(its)
        for i in its:
            self.assertEqual(i["label"], "neg"); self.assertLessEqual(i["fim"] - i["ini"], 3.0 + 1e-6)

    def test_neg_com_verificador_alto_e_descartado(self):
        verif = [0.0] * 375; verif[int(5.1 / 0.08)] = 0.9
        its = r.items_for(src("c.wav", "contexto"), [W("oi", 5.0, 5.3)], sc(30.0, verif=verif), "friday")
        self.assertEqual([(i["label"], i["why"]) for i in its], [(None, "possível palavra mal transcrita")])

    def test_ativou_sem_palavra_localiza_pelo_pico_e_descarta(self):
        verif = [0.0] * 44; verif[20] = 0.9
        its = r.items_for(src("a.wav", "ativou"), [], sc(3.5, verif=verif), "friday")
        self.assertEqual(len(its), 1)
        self.assertAlmostEqual(its[0]["fim"], 21 * 0.08 - 0.30, places=2)
        self.assertAlmostEqual(its[0]["fim"] - its[0]["ini"], 0.60, places=2)
        self.assertIsNone(its[0]["label"])

    def test_ativou_sem_palavra_com_detector_e_verificador_vira_pos(self):
        verif = [0.0] * 44; verif[20] = 0.9
        base = [0.0] * 44; base[20] = 0.8
        its = r.items_for(src("a.wav", "ativou"), [], sc(3.5, base=base, verif=verif), "friday")
        self.assertEqual((its[0]["label"], its[0]["why"]), ("pos", "detector+verificador"))

    def test_ativou_sem_palavra_com_so_um_dos_dois_continua_descartado(self):
        verif = [0.0] * 44; verif[20] = 0.9
        base = [0.0] * 44; base[20] = 0.4
        its = r.items_for(src("a.wav", "ativou"), [], sc(3.5, base=base, verif=verif), "friday")
        self.assertIsNone(its[0]["label"])

    def test_quase_sem_palavra_com_verificador_na_janela_do_pico_vira_pos(self):
        verif = [0.0] * 44; verif[20] = 0.7
        its = r.items_for(src("q.wav", "quase"), [], sc(3.5, verif=verif), "friday")
        self.assertEqual((its[0]["label"], its[0]["why"]), ("pos", "verificador"))
        its = r.items_for(src("q.wav", "quase"), [], sc(3.5, verif=[0.0] * 44), "friday")
        self.assertIsNone(its[0]["label"])

    def test_counts_separa_motivo_dos_positivos(self):
        m = [{"label": "pos", "src": "ativou", "why": "detector+verificador", "split": "train"},
             {"label": "pos", "src": "ativou", "why": "verificador", "split": "train"}]
        self.assertEqual(r.counts(m)["positivos_por_motivo"], {"detector+verificador": 1, "verificador": 1})

    def test_acervo_v_sem_palavra_e_positivo_rotulado(self):
        its = r.items_for(src("v.wav", "acervo_v"), [], sc(3.5, base=[0.0, 0.9] + [0.0] * 42), "friday")
        self.assertEqual((its[0]["label"], its[0]["why"]), ("pos", "rotulado"))

    def test_barrada_sem_palavra_vira_neg_do_arquivo(self):
        its = r.items_for(src("b.wav", "barrada"), [], sc(3.5), "friday")
        self.assertEqual(len(its), 1); self.assertEqual(its[0]["label"], "neg")
        self.assertLessEqual(its[0]["fim"] - its[0]["ini"], 3.0 + 1e-6)

    def test_sessao_tentativas_janelas_e_k(self):
        ws = [W("Friday", 1.8, 2.2)]
        its = r.items_for(src("tentativas_friday_20261008_094900.wav", "sessao"), ws, sc(120.0), "friday")
        self.assertEqual(len(its), 20)
        self.assertEqual([i["k"] for i in its], list(range(20)))
        self.assertTrue(all(i["label"] == "pos" for i in its))
        self.assertEqual((its[0]["ini"], its[0]["fim"]), (1.8, 2.2))   # transcrito
        self.assertAlmostEqual(its[1]["ini"], 1.7 + 6.0 + 0.2, places=2)  # não transcrito: janela + 0,2

    def test_sessao_wav_usa_marcas(self):
        marcas = [{"nome": "tentativa_01", "ini": 0, "fim": 72}, {"nome": "tentativa_02", "ini": 75, "fim": 147},
                  {"nome": "fala_livre", "ini": 150, "fim": 300}]
        its = r.items_for(src("sessao__sessao.wav", "sessao"), [], sc(30.0), "friday", marcas)
        self.assertEqual(len(its), 2)


def mk_ep(i, ts, src_="ativou", lab="pos", ini=2.0):
    it = {"id": f"x{i}@{ini:.2f}", "name": f"x{i}.wav", "src": src_, "ini": ini, "fim": ini + 0.4, "label": lab,
          "why": "x"}
    return it, {"ts": ts, "dur": 3.5, "src": src_}


class TestEpisodios(unittest.TestCase):
    def test_episodes_corta_em_60s(self):
        a, sa = mk_ep(1, "2026-10-24T12:00:00"); b, sb = mk_ep(2, "2026-10-24T12:00:40")
        c, sc_ = mk_ep(3, "2026-10-24T12:02:30")
        e = r.episodes([a, b, c], {"x1.wav": sa, "x2.wav": sb, "x3.wav": sc_})
        self.assertEqual(e[a["id"]], e[b["id"]]); self.assertNotEqual(e[b["id"]], e[c["id"]])

    def test_episodio_contexto_e_ativacao_juntos(self):
        ctx = {"id": "c@28.00", "name": "c.wav", "src": "contexto", "ini": 28.0, "fim": 28.4, "label": "pos", "why": ""}
        atv = {"id": "a@2.00", "name": "a.wav", "src": "ativou", "ini": 2.0, "fim": 2.4, "label": "pos", "why": ""}
        e = r.episodes([ctx, atv], {"c.wav": {"ts": "2026-10-24T12:00:30", "dur": 30.0, "src": "contexto"},
                                    "a.wav": {"ts": "2026-10-24T12:00:30", "dur": 3.5, "src": "ativou"}})
        self.assertEqual(e["c@28.00"], e["a@2.00"])

    def test_sem_ts_e_sessao_tem_episodio_proprio(self):
        a = {"id": "a@1.00", "name": "a.wav", "src": "fala_livre", "ini": 1, "fim": 2, "label": "neg", "why": ""}
        b = {"id": "b@1.00", "name": "b.wav", "src": "sessao", "ini": 1, "fim": 2, "label": "pos", "why": ""}
        e = r.episodes([a, b], {"a.wav": {"ts": None, "dur": 9, "src": "fala_livre"},
                                "b.wav": {"ts": "2026-10-24T12:00:30", "dur": 9, "src": "sessao"}})
        self.assertEqual(e["a@1.00"], "file:a.wav"); self.assertEqual(e["b@1.00"], "sessao:b.wav")


def lote(n_perdida=10, n_funcionou=10, n_neg=0):
    items, eps = [], {}
    for i in range(n_perdida + n_funcionou + n_neg):
        if i < n_perdida:
            s_, l_ = "contexto", "pos"
        elif i < n_perdida + n_funcionou:
            s_, l_ = "ativou", "pos"
        else:
            s_, l_ = "barrada", "neg"
        it = {"id": f"i{i}@1.00", "name": f"i{i}.wav", "src": s_, "ini": 1.0, "fim": 1.4, "label": l_, "why": "x"}
        items.append(it); eps[it["id"]] = f"ep{i}"
    return items, eps


class TestSplit(unittest.TestCase):
    def test_split_episodio_inteiro_de_um_lado(self):
        items, eps = lote()
        extra = {"id": "j@5.00", "name": "j.wav", "src": "ativou", "ini": 5, "fim": 6, "label": "pos", "why": ""}
        items.append(extra); eps[extra["id"]] = "ep3"
        for seed in range(20):
            s = r.split(items, eps, "friday", seed)
            self.assertEqual(s["i3@1.00"], s["j@5.00"])

    def test_split_reprodutivel(self):
        items, eps = lote(30, 30, 10)
        a, b = r.split(items, eps, "friday", 7), r.split(items, eps, "friday", 7)
        self.assertEqual(a, b)
        self.assertNotEqual(a, r.split(items, eps, "friday", 8))

    def test_split_estratificado(self):
        items, eps = lote(10, 10)
        s = r.split(items, eps, "friday", 1)
        hold = [i["src"] for i in items if s[i["id"]] == "holdout"]
        self.assertEqual((hold.count("contexto"), hold.count("ativou")), (2, 2))

    def test_upload_sem_ids_da_reserva(self):
        items, eps = lote(10, 10, 5)
        s = r.split(items, eps, "friday", 3)
        m = {i["id"]: dict(i, split=s[i["id"]]) for i in items}
        reserva = {k for k, v in m.items() if v["split"] == "holdout"}
        self.assertTrue(reserva)
        self.assertEqual(r.upload_ids(m) & reserva, set())
        m["i0@1.00"]["label"] = None; m["i0@1.00"]["split"] = "train"
        self.assertNotIn("i0@1.00", r.upload_ids(m))

    def test_sexta_reserva_k_mod_5(self):
        items = [{"id": f"s@{k}", "name": "s.wav", "src": "sessao", "ini": k, "fim": k + 1, "label": "pos", "why": "",
                  "k": k} for k in range(20)]
        s = r.split(items, {i["id"]: "sessao:s.wav" for i in items}, "sexta_feira", 1)
        self.assertEqual(sorted(int(i.split("@")[1]) for i, v in s.items() if v == "holdout"), [4, 9, 14, 19])

    def test_cut_bounds_e_counts(self):
        p = {"label": "pos", "ini": 1.0, "fim": 1.4}
        self.assertEqual(r.cut_bounds(p, 3.5), (0.8, 1.65))
        self.assertEqual(r.cut_bounds(dict(p, ini=0.1), 1.5), (0.0, 1.5))
        n = {"label": "neg", "ini": 0.0, "fim": 3.0}
        self.assertEqual(r.cut_bounds(n, 2.5), (0.0, 2.5))
        m = [dict(p, src="ativou", why="x", split="train"), dict(n, src="barrada", why="x", split="holdout"),
             {"label": None, "src": "quase", "why": "voz não confirmada", "split": "train"}]
        c = r.counts(m, 3)
        self.assertEqual(c["por_lado"]["train"]["pos"], 1); self.assertEqual(c["por_lado"]["holdout"]["neg"], 1)
        self.assertEqual(c["por_fonte"]["quase"]["descartado"], 1); self.assertEqual(c["duplicatas"], 3)


class TestFalaNaJanela(unittest.TestCase):
    def env(self):
        e = [10.0] * 200  # 4 s de piso
        for i in range(35, 95):  # fala de 0,70 a 1,90 s...
            e[i] = 1000.0
        for i in range(60, 65):  # ...com uma pausa de 0,10 s no meio ("sexta | feira")
            e[i] = 10.0
        for i in range(140, 143):  # estalo a 2,80 s, depois de uma pausa longa: fica de fora
            e[i] = 1000.0
        return e

    def test_palavra_inteira_com_pausa_curta(self):
        self.assertEqual(r.fala_na_janela(self.env(), 10.0, 0.2, 3.8), (0.7, 1.9))

    def test_eco_do_bipe_e_ignorado(self):
        e = self.env()
        e[12] = 1000.0  # eco de um quadro logo no começo da janela
        self.assertEqual(r.fala_na_janela(e, 10.0, 0.2, 3.8), (0.7, 1.9))

    def test_ruido_emendado_e_cortado_no_limite(self):
        e = [10.0] * 200
        for i in range(35, 190):  # "fala" de 3,1 s
            e[i] = 1000.0
        self.assertEqual(r.fala_na_janela(e, 10.0, 0.2, 3.9), (0.7, 2.3))

    def test_silencio_devolve_none(self):
        self.assertIsNone(r.fala_na_janela([10.0] * 200, 10.0, 0.2, 3.8))

    def test_items_for_usa_a_energia_nas_tentativas(self):
        dur = 20.0
        env = {"frames": [10.0] * 1000, "floor": 10.0}
        t = 1.5  # 1ª tentativa: bipe em 1,5 s; fala de 2,1 a 3,5 s (passa da janela fixa de 0,8 s)
        for i in range(int((t + 0.6) / 0.02), int((t + 2.0) / 0.02)):
            env["frames"][i] = 900.0
        src = {"name": "sessao__tentativas_sexta_feira_x.wav", "src": "sessao"}
        scores = {"dur": dur, "base": [0.0] * 250}
        it = r.items_for(src, [], scores, "sexta_feira", None, env)[0]
        self.assertEqual((it["ini"], it["fim"], it["label"]), (2.1, 3.5, "pos"))



class TestPinSplit(unittest.TestCase):
    """Ciclos cumulativos (26/10): quem já teve lado mantém; a prova nunca vira treino."""
    def _its(self):
        return [{"id": "a@1", "name": "a.wav"}, {"id": "b@1", "name": "b.wav"}, {"id": "c@1", "name": "c.wav"},
                {"id": "n@1", "name": "novo.wav"}]

    def test_antigos_mantem_lado_e_novos_ficam_com_sorteio(self):
        its = self._its()
        eps = {"a@1": "e1", "b@1": "e2", "c@1": "e3", "n@1": "e4"}
        sorteio = {"a@1": "holdout", "b@1": "train", "c@1": "train", "n@1": "holdout"}
        ant = r.prev_sides([[{"name": "a.wav", "split": "train"}, {"name": "b.wav", "split": "holdout"}]])
        lado = r.pin_split(sorteio, its, eps, ant)
        self.assertEqual(lado, {"a@1": "train", "b@1": "holdout", "c@1": "train", "n@1": "holdout"})

    def test_episodio_com_arquivo_de_prova_vira_prova_inteiro(self):
        its = self._its()
        eps = {i["id"]: "mesmo" for i in its}
        ant = r.prev_sides([[{"name": "a.wav", "split": "train"}, {"name": "c.wav", "split": "holdout"}]])
        lado = r.pin_split({i["id"]: "train" for i in its}, its, eps, ant)
        self.assertEqual(set(lado.values()), {"holdout"})

    def test_item_ja_visto_herda_o_proprio_lado(self):
        # 27/10: no ciclo 1 da Sexta cada sessão foi dividida por tentativa (16 treino + 4 prova no MESMO arquivo);
        # pelo arquivo, a prova venceria e todas as tentativas iriam para a prova (o treino ficou com 0 positivos)
        its = [{"id": "s@1", "name": "s.wav"}, {"id": "s@2", "name": "s.wav"}, {"id": "s@3", "name": "s.wav"}]
        eps = {i["id"]: "sessao" for i in its}
        m = [[{"id": "s@1", "name": "s.wav", "split": "train"}, {"id": "s@2", "name": "s.wav", "split": "holdout"}]]
        lado = r.pin_split({i["id"]: "train" for i in its}, its, eps, r.prev_sides(m), r.prev_item_sides(m))
        self.assertEqual(lado, {"s@1": "train", "s@2": "holdout", "s@3": "holdout"})

    def test_item_que_foi_prova_em_qualquer_ciclo_continua_prova(self):
        m = [[{"id": "s@1", "split": "holdout"}], [{"id": "s@1", "split": "train"}]]
        self.assertEqual(r.prev_item_sides(m), {"s@1": "holdout"})

    def test_prova_em_qualquer_ciclo_vence(self):
        ant = r.prev_sides([[{"name": "a.wav", "split": "holdout"}], [{"name": "a.wav", "split": "train"}]])
        self.assertEqual(ant, {"a.wav": "holdout"})


class TestFalaAncorada(unittest.TestCase):
    """26/10: Friday picotada sobre música (só a vogal passa do limiar)."""
    def _env(self, picos, n=300, fundo=300.0):
        e = [fundo] * n
        for ini, fim, v in picos:
            for k in range(int(ini / r.ENV_S), int(fim / r.ENV_S)):
                e[k] = v
        return e

    def test_vogal_curta_vira_palavra_inteira(self):
        env = self._env([(2.54, 2.74, 11000.0)])
        self.assertIsNone(r.fala_na_janela(env, 327.0, 1.7, 5.3))  # o defeito: desistia
        self.assertEqual(r.fala_ancorada(env, 327.0, 1.7, 5.3), (2.34, 3.09))

    def test_ancora_e_o_trecho_mais_alto_nao_a_batida(self):
        env = self._env([(1.9, 2.05, 3000.0), (2.6, 2.8, 11000.0)])  # batida antes, voz depois
        ini, fim = r.fala_ancorada(env, 300.0, 1.7, 5.3)
        self.assertEqual(ini, 2.4)

    def test_respeita_a_janela(self):
        env = self._env([(1.75, 1.95, 11000.0)])
        ini, fim = r.fala_ancorada(env, 300.0, 1.7, 5.3)
        self.assertEqual(ini, 1.7)

    def test_sem_nada_acima_do_limiar(self):
        self.assertIsNone(r.fala_ancorada([300.0] * 300, 300.0, 1.7, 5.3))

def _score(dur=3.5, base=0.9, verif=0.1):
    n = int(dur / 0.08)
    return {"dur": dur, "base": [base] * n, "verif": [verif] * n, "vad": [1.0] * n}


class TestSextaAtiva(unittest.TestCase):  # 06/11: disparos e quase-disparos da Sexta em ativa
    def _f(self, src, nome="x_20261106_090000.wav"):
        return {"src": src, "name": f"{src}__{nome}", "ts": "2026-11-06T09:00:00"}

    def _um(self, src, words, **kw):
        its = r.items_for(self._f(src), words, _score(**kw), "sexta_feira")
        self.assertEqual(len(its), 1)
        return its[0]

    def test_ativou_com_voz_confirmada_e_positivo_mesmo_sem_transcricao(self):
        self.assertEqual(self._um("ativou", [W("Sista", 0.5, 0.9), W("foda", 0.9, 1.2)], verif=0.9)["label"], "pos")

    def test_ativou_sem_voz_com_outra_fala_e_negativo(self):
        it = self._um("ativou", [W("reforma", 0.5, 0.9)], verif=0.05)
        self.assertEqual((it["label"], it["why"]), ("neg", "disparo sem a palavra"))
        self.assertAlmostEqual(it["fim"] - it["ini"], 3.0, places=1)

    def test_ativou_sem_voz_e_sem_transcricao_fica_fora(self):
        self.assertIsNone(self._um("ativou", [], verif=0.05)["label"])

    def test_a_palavra_de_outra_voz_e_positivo(self):  # 06/11: a identidade é com o verificador, não com o modelo
        self.assertEqual(self._um("ativou", [W("Sexta-feira", 0.5, 1.2)], verif=0.05)["label"], "pos")

    def test_palavra_mal_transcrita_nao_vira_negativo(self):
        for ws in ([W("Sexta", 0.5, 0.9), W("fira.", 0.9, 1.2)], [W("Sexta", 0.5, 0.9), W("aqui!", 0.9, 1.2)],
                   [W("Se", 0.5, 0.6), W("está", 0.6, 0.8), W("fiel.", 0.9, 1.2)], [W("Esta", 0.5, 0.9), W("feira", 0.9, 1.2)]):
            for src in ("quase", "sombra", "ativou"):
                it = self._um(src, ws, verif=0.05)
                self.assertIsNone(it["label"], (src, ws))
                self.assertEqual(it["why"], "parece a palavra")

    def test_dia_da_semana_continua_negativo(self):
        self.assertEqual(self._um("quase", [W("Hoje", 0.1, 0.3), W("é", 0.3, 0.4), W("quarta-feira", 0.5, 1.2)], verif=0.05)["label"], "neg")
        self.assertEqual(self._um("quase", [W("uma", 0.1, 0.3), W("festa", 0.5, 1.2)], verif=0.05)["label"], "neg")

    def test_quase_sem_palavra_vira_negativo_duro(self):
        self.assertEqual(self._um("quase", [W("quarta-feira", 0.5, 1.2)], verif=0.05)["label"], "neg")

    def test_quase_vazio_vira_negativo(self):
        self.assertEqual(self._um("quase", [], verif=0.05)["label"], "neg")

    def test_quase_que_o_verificador_reconhece_fica_fora(self):
        self.assertIsNone(self._um("quase", [W("Sesta", 0.5, 0.9), W("fera", 0.9, 1.2)], verif=0.9)["label"])

    def test_sombra_que_o_verificador_reconhece_nao_vira_negativo(self):
        self.assertIsNone(self._um("sombra", [W("Se", 0.5, 0.7), W("feio", 0.8, 1.2)], verif=0.82)["label"])

    def test_falsa_do_jose_vence_o_verificador(self):
        self.assertEqual(self._um("acervo_f", [], verif=0.9)["label"], "neg")

    def test_friday_nao_muda(self):
        self.assertEqual(r.label("sombra", False, 0.9, "friday"), ("neg", "sem palavra"))
        self.assertEqual(r.label("quase", False, 0.1, "friday"), (None, "sem palavra transcrita"))


class TestProtegeChamadas(unittest.TestCase):
    INFO = {"ativou__a.wav": {"ts": "2026-11-05T12:15:50", "dur": 3.5, "src": "ativou"},
            "quase__q.wav": {"ts": "2026-11-05T12:15:48", "dur": 3.5, "src": "quase"},
            "quase__l.wav": {"ts": "2026-11-05T12:30:00", "dur": 3.5, "src": "quase"},
            "ativou__b.wav": {"ts": "2026-11-06T09:08:32", "dur": 3.5, "src": "ativou"},
            "quase__b.wav": {"ts": "2026-11-06T09:08:30", "dur": 3.5, "src": "quase"},
            "sessao__s.wav": {"ts": "2026-11-05T12:15:49", "dur": 3.5, "src": "sessao"}}

    def _it(self, name, label):
        return {"id": name + "@0", "name": name, "src": name.split("__")[0], "ini": 0.0, "fim": 1.0, "label": label, "why": "x"}

    def test_chamada_do_ambiente_tira_o_que_e_o_mesmo_audio(self):
        its = [self._it("ativou__a.wav", "pos"), self._it("quase__q.wav", "neg"), self._it("quase__l.wav", "neg"),
               self._it("sessao__s.wav", "pos")]
        n = r.protege_chamadas(its, self.INFO, ["2026-11-05T12:15:49"])
        self.assertEqual([i["label"] for i in its], [None, None, "neg", "pos"])
        self.assertEqual((n, its[0]["why"]), (2, "já nas chamadas do ambiente"))

    def test_quase_colado_em_ativacao_verdadeira_nao_vira_negativo(self):
        its = [self._it("ativou__b.wav", "pos"), self._it("quase__b.wav", "neg")]
        r.protege_chamadas(its, self.INFO, [])
        self.assertEqual([i["label"] for i in its], ["pos", None])


if __name__ == "__main__":
    unittest.main()


class TestNegativosMusica(unittest.TestCase):  # 26/10
    def test_trecho_no_fim_da_janela(self):
        jan = r.recortes_tentativas(130.0)
        neg = r.negativos_musica(jan, {0: 2.6, 1: 8.6})
        self.assertEqual(neg, [(0, round(jan[0][1] - 2.0, 2), jan[0][1]), (1, round(jan[1][1] - 2.0, 2), jan[1][1])])

    def test_palavra_tardia_nao_vira_negativo(self):
        jan = r.recortes_tentativas(130.0)  # janela 0 = (1.7, 7.3): trecho começaria em 5.3
        self.assertEqual(r.negativos_musica(jan, {0: 5.0}), [])

    def test_sem_positivo_na_tentativa_nao_corta(self):
        self.assertEqual(r.negativos_musica(r.recortes_tentativas(130.0), {}), [])

    def test_items_for_so_em_sessao_com_musica(self):
        dur = 130.0
        scores = {"dur": dur, "base": [0.0] * 1700, "verif": []}
        mus = {"name": "sessao__tentativas_friday_music_20261026_152341.wav", "src": "sessao"}
        sem = {"name": "sessao__tentativas_20261020_100000.wav", "src": "sessao"}
        it_m = r.items_for(mus, [], scores, "friday")
        it_s = r.items_for(sem, [], scores, "friday")
        negs = [i for i in it_m if i["label"] == "neg"]
        self.assertEqual(len(negs), 20)
        self.assertTrue(all(i["why"] == "música sem a palavra" for i in negs))
        self.assertEqual(len({i["id"] for i in it_m}), len(it_m))
        self.assertFalse(any(i["label"] == "neg" for i in it_s))

    def test_trecho_com_estalo_nao_e_limpo(self):
        env = [100.0] * 100
        self.assertTrue(r.trecho_limpo(env, 0.0, 2.0))
        env[90] = 100.0 * 10 ** (13 / 20)
        self.assertFalse(r.trecho_limpo(env, 0.0, 2.0))
        self.assertTrue(r.trecho_limpo(env, 0.0, 1.5))


class TestSombra(unittest.TestCase):  # 27/10: disparos do modo sombra da Sexta-feira
    def test_sem_palavra_vira_negativo(self):
        self.assertEqual(r.label("sombra", False, 0.0, "sexta_feira"), ("neg", "sem palavra"))

    def test_com_palavra_fica_fora(self):
        self.assertEqual(r.label("sombra", True, 0.0, "sexta_feira")[0], "pos")  # 06/11: o verificador julga a voz, não a palavra


class TestExcluidas(unittest.TestCase):
    def test_item_de_arquivo_excluido_perde_o_rotulo(self):
        its = [{"id": "sombra__sombra_sexta_feira_20261028_085818.wav@0.00", "name": "sombra__sombra_sexta_feira_20261028_085818.wav",
                "label": "neg", "why": "sem palavra"},
               {"id": "sombra__sombra_sexta_feira_20261029_101010.wav@0.00", "name": "sombra__sombra_sexta_feira_20261029_101010.wav",
                "label": "neg", "why": "sem palavra"}]
        n = r.apply_exclusions(its, ["sombra_sexta_feira_20261028_085818.wav"])
        self.assertEqual(n, 1)
        self.assertEqual((its[0]["label"], its[0]["why"]), (None, "excluída pelo o dono"))
        self.assertEqual(its[1]["label"], "neg")

