"""Decisão do verificador pessoal da palavra (spec 2026-10-15 D11, D14, D17): dobras por sessão, simulação do
detector quadro a quadro (igual à captura) e o portão de aprovação. Lógica pura, sem numpy: roda no HA e na Pi
(test_verificador.py). Quem extrai as características e treina é o treino_verificador.py, só na Pi."""
from detectores import DetectorPalavra

LIMIARES_VERIFICADOR = (0.2, 0.3, 0.4, 0.5)  # 0,5 só filtra o que a base já aceita: não cria disparo novo
LIMIARES_PALAVRA = (0.5, 0.6, 0.7)
CORTE_POSITIVO = 0.3                 # D11: positivos a partir de 0,3 (a biblioteca usa 0,5)
CORTE_PALAVRA_NA_FALA_LIVRE = 0.5    # "Friday" dito sem querer na fala livre
VIZINHOS = 12                        # ~1 s em volta dele sai dos negativos
JANELA_VOZ = 6                       # igual à captura: maior voz dos últimos 6 pedaços de 80 ms
DOBRAS = 5
# D17 (revisado em 15/10 depois da avaliação prévia e de uma revisão independente): a referência é o detector de hoje
# (só a base, mesma nota e porta de voz) nos MESMOS clipes. O "50/60" do spec era uma contagem ao vivo que o replay
# offline não reproduz (o modelo base sozinho dá 30/60), e o verificador nunca passa do teto que o limiar permite.
REFERENCIA_PALAVRA = 0.5             # limiar da palavra de hoje: toda comparação "não piorar" é contra a produção
MIN_NOVA = 0.8                       # tentativas da pessoa nova aceitas (decisão de produto: 8 de 10)
FALSAS_PASSAM_MAX = 0.2              # das falsas que a base aceita, no máximo esta fração pode sobrar (decisão de produto)
TOLERANCIA_VERDADEIRAS = 3           # ativações verdadeiras que o verificador pode perder em relação à base (produto)
OUVIU_MAL_MAX = 2                    # mais que isto sem nenhum quadro >= 0,3: não dá para chegar a 8 de 10


def dobras(grupos, k=DOBRAS):
    return {g: i % k for i, g in enumerate(sorted(set(grupos)))}


def quadros_positivos(base, corte=CORTE_POSITIVO):
    return [i for i, b in enumerate(base) if b >= corte]


def quadros_fala_livre(base):
    perto = set()
    for i, b in enumerate(base):
        if b >= CORTE_PALAVRA_NA_FALA_LIVRE:
            perto.update(range(i - VIZINHOS, i + VIZINHOS + 1))
    return [i for i in range(len(base)) if i not in perto]


def aceita(base, notas, vad, lv, limiar, limiar_voz):
    """O que a captura faria: nota do verificador quando a base >= lv, porta de voz e DetectorPalavra."""
    d = DetectorPalavra(limiar, limiar_voz)
    for i, b in enumerate(base):
        s = notas[i] if b >= lv else b
        if d.alimentar(s, max(vad[max(0, i - JANELA_VOZ + 1): i + 1])):
            return True
    return False


def avaliar(clipes, treinar, notas_de, limiar_voz, k=DOBRAS):
    """Validação cruzada por grupo (sessão): cada clipe é avaliado só pelo modelo que não viu o grupo dele.
    clipes: dicts com "cat", "grupo", "base", "vad", "rotulo" e "quadros". treinar(lista) → modelo;
    notas_de(modelo, clipe) → nota do verificador por quadro. Devolve {(lv, limiar): {cat: [aceitos, total]}} e,
    por limiar da palavra, a referência {("base", limiar): {cat: [aceitos, total]}} (só a base, sem treinar)."""
    d = dobras([c["grupo"] for c in clipes], k)
    tabela = {(lv, lp): {} for lv in LIMIARES_VERIFICADOR for lp in LIMIARES_PALAVRA}
    for lp in LIMIARES_PALAVRA:
        cats = tabela.setdefault(("base", lp), {})
        for c in clipes:
            par = cats.setdefault(c["cat"], [0, 0])
            par[0] += aceita(c["base"], c["base"], c["vad"], 2.0, lp, limiar_voz)  # lv > 1: nunca usa o verificador
            par[1] += 1
    for f in range(k):
        fora = [c for c in clipes if d[c["grupo"]] == f]
        if not fora:
            continue
        modelo = treinar([c for c in clipes if d[c["grupo"]] != f])
        for c in fora:
            notas = notas_de(modelo, c)
            for (lv, lp), cats in tabela.items():
                if lv == "base":
                    continue
                par = cats.setdefault(c["cat"], [0, 0])
                par[0] += aceita(c["base"], notas, c["vad"], lv, lp, limiar_voz)
                par[1] += 1
    return tabela


def criterios(cats, antes, base=None):
    """(ok, texto) na ordem do D17. `antes`: {id: tentativas aceitas no cadastro anterior}. `base`: as mesmas
    categorias medidas só com o detector de hoje (a referência de "não piorar")."""
    base = base or {}
    r = []
    nova = cats.get("nova")
    if nova:
        r.append((nova[0] >= MIN_NOVA * nova[1], f"tentativas da pessoa nova aceitas: {nova[0]} de {nova[1]}"))
    f, bf = cats.get("falsas"), base.get("falsas")
    if f and bf:
        r.append((f[0] <= FALSAS_PASSAM_MAX * bf[0] + 1e-9,
                  f"disparos falsos que sobraram: {f[0]} de {f[1]} (hoje {bf[0]})"))
    for ident, a in sorted(antes.items()):
        p = cats.get(f"pessoa:{ident}")
        if p and a is not None:
            r.append((p[0] >= a - 1, f"tentativas de {ident} aceitas: {p[0]} (antes {a})"))
    v, bv = cats.get("verdadeiras"), base.get("verdadeiras")
    if v and bv:
        r.append((v[0] >= bv[0] - TOLERANCIA_VERDADEIRAS,
                  f"ativações verdadeiras aceitas: {v[0]} de {v[1]} (hoje {bv[0]})"))
    h, bh = cats.get("historico"), base.get("historico")
    if h and bh:
        r.append((h[0] >= bh[0], f"tentativas antigas do dono aceitas: {h[0]} de {h[1]} (hoje {bh[0]})"))
    fl, bfl = cats.get("fala_livre"), base.get("fala_livre")
    if fl and bfl:
        r.append((fl[0] <= bfl[0], f"disparos na fala livre: {fl[0]} (hoje {bfl[0]})"))
    return r


def escolher(tabela, antes):
    """(aprovado, (lv, limiar), textos, falhou). Entre as combinações que passam: o limiar do verificador mais alto
    (só filtra o que a base já aceita; limiar baixo julga notas que nenhum dado de avaliação cobre), depois a que deixa
    passar menos falsas e a que aceita mais. Sem nenhuma: a que cumpre mais critérios, e `falhou` é o primeiro
    critério que ela não cumpre."""
    melhor = None
    for combo, cats in sorted((k, v) for k, v in tabela.items() if k[0] != "base"):
        cs = criterios(cats, antes, tabela.get(("base", REFERENCIA_PALAVRA)))
        oks = sum(1 for ok, _ in cs if ok)
        f = cats.get("falsas", [0, 0])
        aceitos = sum(p[0] for c, p in cats.items() if c not in ("falsas", "fala_livre"))
        chave = (oks == len(cs), oks, combo[0], -f[0], aceitos)
        if melhor is None or chave > melhor[0]:
            melhor = (chave, combo, cs)
    chave, combo, cs = melhor
    return chave[0], combo, [t for _, t in cs], next((t for ok, t in cs if not ok), None)


def ouviu_mal(bases):
    """Quantas tentativas não têm nenhum quadro >= CORTE_POSITIVO no modelo base."""
    return sum(1 for b in bases if not quadros_positivos(b))


def recortes_tentativas(duracao_s, intervalo=6.0, inicio=1.5, pausa_metade=4.9):
    """Janelas (início, fim) das tentativas numa gravação do teste_tentativas.py: 1º bipe 1,5 s depois do começo,
    um a cada 6 s; na metade, tom grave (~0,9 s) + 4 s de pausa. N = 10 nas gravações curtas, 20 nas longas."""
    n = 10 if duracao_s < 90 else 20
    out = []
    for k in range(n):
        t = inicio + k * intervalo + (pausa_metade if k >= n // 2 else 0)
        out.append((round(t + 0.2, 2), round(min(t + intervalo - 0.2, duracao_s), 2)))
    return out
