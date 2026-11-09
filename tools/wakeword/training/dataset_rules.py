"""Regras puras do dataset de retreino (spec/plano 2026-10-24, Task 4): rótulo, recorte, episódio, sorteio da prova.
Sem numpy (roda no HA e na VM). IO de áudio fica em build_dataset.py. Testes: test_dataset_rules.py.
Convenção: tempos em segundos; os quadros de nota têm 80 ms e o quadro k cobre o áudio que termina em (k+1)*0.08 s."""
import datetime, random, re

FRAME_S = 0.08
WAKE = {"friday": r"^(friday|freddy|freddie|fred|fraide|fraidei)$"}
_PONTUACAO = " \t.,!?¿¡\"'…;:"
FONTES_NEG = {"barrada", "sem_voz", "acervo_f", "fala_livre", "contexto", "sombra"}
COM_VERIFICADOR = ("friday", "sexta_feira")  # 06/11: a Sexta ganhou verificador de voz; a nota dele vale como rótulo
LIMIAR_VERIF = 0.5
# 06/11: o Whisper erra a Sexta ("Sexta fira", "Se está fiel"): negativo só com a certeza de que a palavra não está lá
PARECE_SEXTA = re.compile(r"\bsexta\b|\b(se[sx]?t\w*|s[iy]s?t\w*|sex\w*|testa|esta|desta|nesta|se est[aá])[\s-]*f\w*", re.I)


def _norm(w) -> str:
    return w.strip(_PONTUACAO).lower() if isinstance(w, str) else ""


def wake_spans(words, model):
    """[(ini, fim, indice_da_primeira_palavra)] das palavras-alvo transcritas. `words`: [{"w","i","f","p"}]."""
    out, n, idx = [], len(words), 0
    while idx < n:
        w = _norm(words[idx]["w"])
        if model == "friday":
            if re.match(WAKE["friday"], w):
                out.append((words[idx]["i"], words[idx]["f"], idx))
        elif model == "sexta_feira":
            if w in ("sexta-feira", "sextafeira"):
                out.append((words[idx]["i"], words[idx]["f"], idx))
            elif w == "sexta" and idx + 1 < n and _norm(words[idx + 1]["w"]).lstrip("-") == "feira":
                out.append((words[idx]["i"], words[idx + 1]["f"], idx))
                idx += 1
        idx += 1
    return out


def has_wake(words, i, model="friday") -> bool:
    """True se uma palavra-alvo COMEÇA na palavra de índice i."""
    return any(s[2] == i for s in wake_spans(words, model))


def window_max(arr, ini, fim) -> float:
    """Maior arr[k] para k de int((ini-0.2)/0.08) a int((fim+1.2)/0.08) (inclusive, limitado ao array).
    Mesma regra do wakeword-analysis/report.py: o modelo responde com atraso, até ~1,2 s depois da palavra."""
    k0, k1 = max(int((ini - 0.2) / FRAME_S), 0), int((fim + 1.2) / FRAME_S) + 1
    trecho = arr[k0:k1]
    return float(max(trecho)) if trecho else 0.0


def label(src, has_word, verif_max, model):
    if has_word:
        if src == "acervo_f":  # o o dono rotulou como falsa; uma palavra transcrita ali não vira positivo (ruling)
            return None, "falsa rotulada com a palavra"
        if src in ("acervo_v", "sessao"):
            return "pos", "rotulado"
        if model in COM_VERIFICADOR and verif_max >= LIMIAR_VERIF:
            return "pos", "verificador"
        if model == "sexta_feira":  # 06/11 (o dono): o modelo aprende a palavra; quem julga a identidade é o verificador
            return "pos", "palavra transcrita"
        return None, "voz não confirmada"
    if src in FONTES_NEG or (src == "quase" and model == "sexta_feira"):
        # 06/11: "Se está feio" da sombra de 27/10 era chamada real e virou negativo ×10. Na Sexta, o Whisper errar a
        # palavra não basta para negativo: se o verificador reconhece a voz, fica fora. Só o rótulo do o dono (acervo_f) vence.
        if model == "sexta_feira" and src != "acervo_f" and verif_max >= LIMIAR_VERIF:
            return None, "possível chamada"
        return "neg", "sem palavra"
    return None, "sem palavra transcrita"


def recortes_tentativas(duracao_s, intervalo=6.0, inicio=1.5, pausa_metade=4.9):
    """Janelas (início, fim) das tentativas numa gravação do teste_tentativas.py (copiado de programa/verificador.py)."""
    n = 10 if duracao_s < 90 else 20
    out = []
    for k in range(n):
        t = inicio + k * intervalo + (pausa_metade if k >= n // 2 else 0)
        out.append((round(t + 0.2, 2), round(min(t + intervalo - 0.2, duracao_s), 2)))
    return out


ENV_S = 0.02  # passo do envelope de energia (build_dataset.envelope)


def fala_na_janela(env, piso, j0, j1, lacuna=0.3, limite=1.6, minimo=0.25):
    """(ini, fim) da fala numa janela de tentativa guiada, pela energia (25/10: a janela fixa de 0,8 s cortava
    "Sexta-feira" ao meio em 37% dos recortes). `env`: RMS em quadros de ENV_S s; `piso`: ruído de fundo do arquivo.
    Limiar = max(4 * piso, 10% do pico da janela). Trechos acima do limiar se juntam enquanto as pausas forem
    <= `lacuna` (o "t" e o "f" de sexta-feira); vale o 1º trecho com >= `minimo` s (o eco do bipe fica de fora),
    cortado em `limite` s. None se nenhum trecho serve."""
    k0, k1 = max(int(j0 / ENV_S), 0), min(int(j1 / ENV_S), len(env))
    trecho = env[k0:k1]
    if not trecho:
        return None
    thr = max(4 * piso, 0.1 * max(trecho))
    acima = [i for i, v in enumerate(trecho) if v > thr]
    grupos = []
    for i in acima:
        if grupos and (i - grupos[-1][1]) * ENV_S <= lacuna:
            grupos[-1][1] = i
        else:
            grupos.append([i, i])
    for a, b in grupos:
        if (b + 1 - a) * ENV_S >= minimo:
            b = min(b, a + int(limite / ENV_S) - 1)
            return round((k0 + a) * ENV_S, 2), round((k0 + b + 1) * ENV_S, 2)
    return None


def fala_ancorada(env, piso, j0, j1, dur_min=0.55, antes=0.2, depois=0.35, minimo=0.1, lacuna=0.3, limite=1.6):
    """Friday com música (26/10): o fundo alto sobe o limiar de `fala_na_janela` e só a vogal forte passa (0,12–0,2 s),
    então ela desistia e o recorte caía na janela fixa, que cortava o "day". Aqui o trecho MAIS ALTO da janela (a vogal,
    acima do limiar por >= `minimo` s) é a âncora; o recorte começa `antes` s antes dela (o "Fr") e vai até cobrir
    `depois` s após a âncora e no mínimo `dur_min` s (o "day", que o Jabra picota). None se nada passa do limiar."""
    k0, k1 = max(int(j0 / ENV_S), 0), min(int(j1 / ENV_S), len(env))
    trecho = env[k0:k1]
    if not trecho:
        return None
    thr = max(4 * piso, 0.1 * max(trecho))
    grupos = []
    for i, v in enumerate(trecho):
        if v <= thr:
            continue
        if grupos and (i - grupos[-1][1]) * ENV_S <= lacuna:
            grupos[-1][1] = i
        else:
            grupos.append([i, i])
    grupos = [g for g in grupos if (g[1] + 1 - g[0]) * ENV_S >= minimo]
    if not grupos:
        return None
    a, b = max(grupos, key=lambda g: max(trecho[g[0]:g[1] + 1]))
    ini = max(j0, (k0 + a) * ENV_S - antes)
    fim = min(j1, max((k0 + b + 1) * ENV_S + depois, ini + dur_min), ini + limite)
    return round(ini, 2), round(fim, 2)


def negativos_musica(janelas, fim_pos, comprimento=2.0, folga=0.5):
    """Trechos só de música numa sessão guiada com música (26/10): em cada tentativa, os `comprimento` s que terminam no
    fim da janela (0,2 s antes do bipe seguinte), desde que comecem `folga` s depois do fim da palavra.
    `fim_pos`: k -> fim da palavra (s). Devolve [(k, ini, fim)]. Sem música não há modelo que aprenda "música não é
    Friday": sem estes negativos, dar mais peso aos positivos com música ensina "música = Friday"."""
    out = []
    for k, (j0, j1) in enumerate(janelas):
        ini = j1 - comprimento
        if k in fim_pos and ini >= fim_pos[k] + folga and ini >= j0:
            out.append((k, round(ini, 2), round(j1, 2)))
    return out


def trecho_limpo(env, ini, fim, salto_db=12.0):
    """Nenhum quadro do envelope (ENV_S s) passa `salto_db` acima da mediana do trecho (26/10: 3 de 99 trechos de
    música tinham um estalo/sílaba de 0,1–0,2 s, -17 dB sobre -41 dB). Trecho assim não vira negativo."""
    q = sorted(env[int(ini / ENV_S):int(fim / ENV_S)])
    if not q:
        return False
    med = q[len(q) // 2]
    return q[-1] <= med * 10 ** (salto_db / 20)


def _verif_max(scores, ini, fim):
    return window_max(scores.get("verif") or [], ini, fim)


def _item(source, ini, fim, has_word, scores, model, k=None):
    ini, fim = round(max(ini, 0.0), 2), round(min(fim, scores["dur"]), 2)
    lab, why = label(source["src"], has_word, _verif_max(scores, ini, fim), model)
    d = {"id": f"{source['name']}@{ini:.2f}", "name": source["name"], "src": source["src"], "ini": ini, "fim": fim,
         "label": lab, "why": why}
    if k is not None:
        d["k"] = k
    return d


def _grupos_neg(words, spans):
    """Palavras NÃO-alvo agrupadas enquanto o intervalo for < 0,5 s, cada grupo com no máximo 3,0 s, e a pelo menos
    1,0 s de qualquer palavra-alvo. Devolve [(ini, fim)]."""
    alvo = {s[2] for s in spans}
    for s in spans:  # 'sexta feira' ocupa duas palavras
        alvo.update(i for i, w in enumerate(words) if s[0] <= w["i"] and w["f"] <= s[1] + 1e-6)
    livres = sorted((w for i, w in enumerate(words) if i not in alvo), key=lambda w: w["i"])
    grupos, atual = [], None
    for w in livres:
        if atual and w["i"] - atual[1] < 0.5 and w["f"] - atual[0] <= 3.0:
            atual[1] = max(atual[1], w["f"])
        else:
            atual = [w["i"], w["f"]]
            grupos.append(atual)
    return [(a, min(b, a + 3.0)) for a, b in grupos
            if not any(a < s[1] + 1.0 and b > s[0] - 1.0 for s in spans)]


def _pico(scores, model):
    ref = scores.get("verif") if (model == "friday" and scores.get("verif")) else scores.get("base") or []
    return max(range(len(ref)), key=lambda k: ref[k]) if ref else 0


def items_for(source, words, scores, model, marcas=None, env=None):
    """Transforma um arquivo em itens {"id","name","src","ini","fim","label","why"[,"k"]} (ver o plano, Task 4)."""
    itens = _itens(source, words, scores, model, marcas, env)
    if model == "sexta_feira" and source["src"] in ("ativou", "quase", "sombra") and _parece_sexta(words):
        for it in itens:
            if it["label"] == "neg":
                it["label"], it["why"] = None, "parece a palavra"
    return itens


def _itens(source, words, scores, model, marcas, env):
    src, dur = source["src"], scores["dur"]
    spans = wake_spans(words, model)
    itens = []
    if src == "sessao":
        if "tentativas_" in source["name"]:
            janelas = recortes_tentativas(dur)
        else:
            janelas = [(m["ini"] * FRAME_S, m["fim"] * FRAME_S) for m in (marcas or [])
                       if str(m.get("nome", "")).startswith("tentativa_")]
        for k, (j0, j1) in enumerate(janelas):
            dentro = [s for s in spans if j0 - 0.2 <= s[0] < j1]
            fala = fala_na_janela(env["frames"], env["floor"], j0, j1) if env else None
            if env and model == "friday" and (fala is None or fala[1] - fala[0] < 0.55):  # 26/10: palavra picotada
                fala = fala_ancorada(env["frames"], env["floor"], j0, j1) or fala
            if fala:  # a energia acha a palavra inteira; o Whisper erra o tempo ou nem a reconhece (25/10)
                itens.append(_item(source, fala[0], fala[1], True, scores, model, k))
            elif dentro:
                itens.append(_item(source, dentro[0][0], dentro[0][1], True, scores, model, k))
            else:  # tentativa guiada sem transcrição: a palavra veio logo depois do bipe
                itens.append(_item(source, j0 + 0.2, j0 + 1.0, True, scores, model, k))
        if "_music_" in source["name"]:
            fim_pos = {it["k"]: it["fim"] for it in itens}
            for k, a, b in negativos_musica(janelas, fim_pos):
                if env and not trecho_limpo(env["frames"], a, b):
                    continue
                itens.append({"id": f"{source['name']}@{a:.2f}", "name": source["name"], "src": src, "ini": a,
                              "fim": b, "label": "neg", "why": "música sem a palavra", "k": k})
        return itens
    if src in ("contexto", "fala_livre"):
        for a, b, _ in spans:
            if src == "contexto":
                itens.append(_item(source, a, b, True, scores, model))
        for a, b in _grupos_neg(words, spans):
            # palavra-alvo possivelmente mal transcrita (o verificador a reconhece): não vira negativo (ruling)
            if model == "friday" and _verif_max(scores, a, b) >= 0.5:
                itens.append({"id": f"{source['name']}@{a:.2f}", "name": source["name"], "src": src, "ini": round(a, 2),
                              "fim": round(b, 2), "label": None, "why": "possível palavra mal transcrita"})
            else:
                itens.append(_item(source, a, b, False, scores, model))
        return itens
    # ativou / quase / acervo_* / barrada / sem_voz
    for a, b, _ in spans:
        itens.append(_item(source, a, b, True, scores, model))
    if spans:
        return itens
    k = _pico(scores, model)
    if model == "sexta_feira" and src == "ativou" and words and not _voz_confirmada(scores):
        it = _janela_negativa(source, scores, model)  # disparo com outra fala e sem a voz do o dono: o falso que mais ensina
        it["label"], it["why"] = "neg", "disparo sem a palavra"
        return itens + [it]
    if src in ("ativou", "acervo_v") or (src == "quase" and model == "friday"):
        fim = (k + 1) * FRAME_S - 0.30
        ini = fim - (0.60 if model == "friday" else 0.90)  # "sexta-feira" é mais longa: 0,6 s cortaria a palavra
        tem = src == "acervo_v"
        if src == "quase" and model == "friday":  # decisão do controlador (24/10): verificador >= 0,5 na janela do pico basta
            tem = _verif_max(scores, max(ini, 0.0), fim) >= 0.5
        it = _item(source, ini, fim, tem, scores, model)
        if src == "ativou" and model in COM_VERIFICADOR and it["label"] is None and _voz_confirmada(scores):
            # decisão do controlador (24/10): o detector (nota base) e o verificador já aceitaram: vale sem a palavra
            it["label"], it["why"] = "pos", "detector+verificador"
        itens.append(it)
    else:  # barrada / sem_voz / acervo_f sem a palavra: o arquivo inteiro (até 3 s) em torno do pico do modelo base
        itens.append(_janela_negativa(source, scores, model))
    return itens


def _parece_sexta(words):
    return bool(PARECE_SEXTA.search(" ".join(w["w"].strip(_PONTUACAO) for w in words)))


def _voz_confirmada(scores):
    return (max(scores.get("verif") or [0.0]) >= LIMIAR_VERIF and max(scores.get("base") or [0.0]) >= LIMIAR_VERIF)


def _janela_negativa(source, scores, model):
    dur, base = scores["dur"], scores.get("base") or []
    kb = max(range(len(base)), key=lambda i: base[i]) if base else 0
    ini = max(0.0, min(kb * FRAME_S - 1.5, dur - 3.0))
    return _item(source, ini, min(dur, ini + 3.0), False, scores, model)


def protege_chamadas(items, sources, chamadas, janela=6.0, janela_ativou=15.0):
    """06/11: tira do treino o que é a mesma fala de uma chamada real, para ela não entrar duas vezes (nem cruzar
    treino e prova) nem virar negativo. `chamadas`: instantes ISO das chamadas do ambiente (manifesto de
    raw/<data>_ambient_calls). Vale para ativou/quase/sombra a até `janela` s. Além disso, negativo de quase/sombra a até
    `janela_ativou` s de uma ativação verdadeira fica fora: é a tentativa anterior do o dono. Devolve quantos itens mudaram."""
    def ts(nome):
        return datetime.datetime.fromisoformat(sources[nome]["ts"]).timestamp()

    ref = [datetime.datetime.fromisoformat(c).timestamp() for c in chamadas]
    n = 0
    for it in items:
        if it["src"] in ("ativou", "quase", "sombra") and it["label"] is not None and sources[it["name"]].get("ts") \
                and any(abs(ts(it["name"]) - c) <= janela for c in ref):
            it["label"], it["why"] = None, "já nas chamadas do ambiente"
            n += 1
    verdadeiras = [ts(i["name"]) for i in items if i["src"] == "ativou" and i["label"] == "pos"]
    for it in items:
        if it["src"] in ("quase", "sombra") and it["label"] == "neg" and sources[it["name"]].get("ts") \
                and any(abs(ts(it["name"]) - c) <= janela_ativou for c in verdadeiras):
            it["label"], it["why"] = None, "tentativa perto de ativação"
            n += 1
    return n


def _abs_t(item, src_info):
    ts = datetime.datetime.fromisoformat(src_info["ts"])
    return ts.timestamp() - src_info["dur"] + item["ini"]


def episodes(items, sources):
    """id do item -> id do episódio. `sources`: nome -> {"ts","dur","src"}. Itens a menos de 60 s um do outro (em
    ordem de instante absoluto) são o mesmo episódio; sem ts, o arquivo é o episódio; cada sessão é um episódio."""
    out, datados = {}, []
    for it in items:
        info = sources[it["name"]]
        if info["src"] == "sessao":
            out[it["id"]] = f"sessao:{it['name']}"
        elif not info.get("ts"):
            out[it["id"]] = f"file:{it['name']}"
        else:
            datados.append((_abs_t(it, info), it["id"]))
    datados.sort()
    ep, ultimo = None, None
    for t, iid in datados:
        if ultimo is None or t - ultimo > 60.0:
            ep = f"ep:{datetime.datetime.fromtimestamp(t, datetime.timezone.utc):%Y%m%dT%H%M%S}"
        out[iid] = ep
        ultimo = t
    return out


def _estrato(itens_do_ep):
    pos = [i for i in itens_do_ep if i["label"] == "pos"]
    if any(i["src"] in ("contexto", "quase") for i in pos):
        return "perdida"
    return "funcionou" if pos else "negativo"


def split(items, eps, model, seed, frac=0.2):
    """id do item -> "train" | "holdout". Todo item herda o lado do seu episódio (friday); na sexta_feira a prova são
    as tentativas de índice k % 5 == 4."""
    lado = {}
    if model == "sexta_feira":
        for it in items:
            lado[it["id"]] = "holdout" if (it["src"] == "sessao" and it.get("k") is not None and it["k"] % 5 == 4) \
                else "train"
        return lado
    por_ep = {}
    for it in items:
        por_ep.setdefault(eps[it["id"]], []).append(it)
    estratos = {}
    for ep, its in por_ep.items():
        if any(i["label"] is not None for i in its):  # episódio sem nada utilizável não entra no sorteio
            estratos.setdefault(_estrato(its), []).append(ep)
    rng, reserva = random.Random(seed), set()
    for nome in sorted(estratos):
        lista = sorted(estratos[nome])
        rng.shuffle(lista)
        reserva.update(lista[:round(frac * len(lista))])
    for it in items:
        lado[it["id"]] = "holdout" if eps[it["id"]] in reserva else "train"
    return lado


def prev_sides(manifests):
    """nome do arquivo -> lado dele nos ciclos anteriores (26/10). Arquivo que já foi prova em algum ciclo continua prova."""
    out = {}
    for m in manifests:
        for it in m:
            if it.get("split") in ("train", "holdout"):
                out[it["name"]] = "holdout" if "holdout" in (it["split"], out.get(it["name"])) else it["split"]
    return out


def prev_item_sides(manifests):
    """id do item -> lado dele nos ciclos anteriores (27/10). Item que já foi prova em algum ciclo continua prova."""
    out = {}
    for m in manifests:
        for it in m:
            if it.get("id") and it.get("split") in ("train", "holdout"):
                out[it["id"]] = "holdout" if "holdout" in (it["split"], out.get(it["id"])) else it["split"]
    return out


def pin_split(lado, items, eps, anteriores, anteriores_itens=None):
    """Os ciclos são cumulativos (26/10): o episódio que tem algum arquivo já usado num ciclo anterior herda o lado dele,
    e a prova vence (nada que foi prova vira treino). Só episódios inteiramente novos ficam com o sorteio de `split`.
    Sem isto, cada material novo reembaralha o sorteio e a prova antiga vaza para o treino."""
    ep_lado = {}
    for it in items:
        s = anteriores.get(it["name"])
        if s:
            e = eps[it["id"]]
            ep_lado[e] = "holdout" if "holdout" in (s, ep_lado.get(e)) else s
    # 27/10: o item (mesmo arquivo, mesma janela) já visto herda o PRÓPRIO lado; o resto do episódio segue a regra acima
    ai = anteriores_itens or {}
    return {it["id"]: ai.get(it["id"]) or ep_lado.get(eps[it["id"]], lado[it["id"]]) for it in items}


def apply_exclusions(items, nomes):
    """02/11: arquivos que o o dono mandou deixar de fora (rulings.json → "excluidas", nome original do arquivo, sem o
    prefixo `<src>__`), p. ex. testes dele com a palavra em sombra que o Whisper não reconheceu (virariam negativos).
    O item fica sem rótulo (não sobe, não conta na prova). Devolve quantos itens foram afetados."""
    alvo, n = set(nomes), 0
    for it in items:
        if it["name"].split("__", 1)[-1] in alvo and it["label"] is not None:
            it["label"], it["why"] = None, "excluída pelo o dono"
            n += 1
    return n


def upload_ids(manifest):
    """Os id que podem subir para o Kaggle: lado de treino e com rótulo."""
    itens = manifest.values() if isinstance(manifest, dict) else manifest
    return {i["id"] for i in itens if i["split"] == "train" and i["label"] is not None}


def cut_bounds(item, dur):
    """Janela do recorte de áudio. Positivos: 0,20 s antes e 0,25 s depois da palavra; negativos: ini/fim do item."""
    if item["label"] == "pos":
        return max(0.0, item["ini"] - 0.20), min(dur, item["fim"] + 0.25)
    return item["ini"], min(dur, item["fim"])


def counts(manifest, duplicatas=0):
    positivos_por_motivo = {}
    por_fonte, por_lado, motivos = {}, {"train": {"pos": 0, "neg": 0}, "holdout": {"pos": 0, "neg": 0}}, {}
    for it in manifest:
        f = por_fonte.setdefault(it["src"], {"pos": 0, "neg": 0, "descartado": 0})
        if it["label"] is None:
            f["descartado"] += 1
            motivos[it["why"]] = motivos.get(it["why"], 0) + 1
        else:
            f[it["label"]] += 1
            if it["label"] == "pos":
                positivos_por_motivo[it["why"]] = positivos_por_motivo.get(it["why"], 0) + 1
            por_lado[it["split"]][it["label"]] += 1
    return {"por_fonte": por_fonte, "por_lado": por_lado, "positivos_por_motivo": positivos_por_motivo,
            "descartes_por_motivo": motivos, "duplicatas": duplicatas}
