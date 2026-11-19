"""Funções puras da coleta (spec 2026-10-24): nome único, instante e eliminação de duplicatas por conteúdo."""
import datetime, os, re

# prioridade quando o MESMO áudio (mesmo sha1) aparece em mais de uma fonte: a primeira da lista vence (Foco 4)
PRIORIDADE = ("acervo_v", "acervo_f", "sessao", "ativou", "quase", "contexto", "barrada", "sem_voz", "fala_livre", "sombra")


SAT_ID = re.compile(r"sat-[0-9a-f]{6}")


def nome_unico(src: str, caminho: str, sat: str = None) -> str:
    """`<src>__<arquivo>`; com `sat` (18/11/2026), `<sat_id>__<src>__<arquivo>`: a origem segue o clipe fora da pasta."""
    base = f"{src}__{os.path.basename(caminho)}"
    return f"{sat}__{base}" if sat else base


def ts_do_nome(caminho: str):
    """O ÚLTIMO AAAAMMDD_HHMMSS do nome, em ISO; None se não houver (ou for data impossível)."""
    achados = re.findall(r"(\d{8})_(\d{6})", os.path.basename(caminho))
    if not achados:
        return None
    d, h = achados[-1]
    try:
        return datetime.datetime.strptime(d + h, "%Y%m%d%H%M%S").isoformat()
    except ValueError:
        return None


def deduplicar(linhas, sat=None):
    """linhas: [(src, caminho, sha1)]. Devolve (fontes, descartadas): `fontes` = [{"src","path","name","ts","sha1"[,"sat"]}]
    com um item por sha1 (fica a fonte de maior prioridade), na ordem de entrada; `descartadas` = quantas sobraram.
    `sat` (18/11/2026): satélite de onde as linhas vieram; vai no nome (nome_unico) e no campo "sat"."""
    if sat is not None and not SAT_ID.fullmatch(sat):
        raise ValueError(f"sat_id inválido: {sat!r}")
    melhor = {}
    for src, caminho, sha in linhas:
        if src not in PRIORIDADE:
            raise ValueError(f"src desconhecido: {src}")
        atual = melhor.get(sha)
        if atual is None or PRIORIDADE.index(src) < PRIORIDADE.index(atual[0]):
            melhor[sha] = (src, caminho)
    fontes = []
    for src, caminho, sha in linhas:
        if melhor[sha] == (src, caminho):
            f = {"src": src, "path": caminho, "name": nome_unico(src, caminho, sat), "ts": ts_do_nome(caminho), "sha1": sha}
            if sat:
                f["sat"] = sat
            fontes.append(f)
            melhor[sha] = None  # o mesmo (src, caminho) repetido na entrada não duplica
    return fontes, len(linhas) - len(fontes)


def falsas_rotuladas(rulings, sat=None, office=None) -> list:
    """Nomes das ativações (`ativacoes/AAAAMMDD_HHMMSS.wav`) que o dono ouviu e rotulou como falsas (31/10), lidos de
    `/share/wakeword-data/<modelo>/rulings.json` → "falsas_ativacoes". Só o nome do clipe da ativação: o nome vai na linha
    de comando do ssh e o contexto_ segue as regras próprias dele (os trechos sem a palavra já viram negativos)."""
    return _validados(rulings, "falsas_ativacoes", sat, office)


def verdadeiras_rotuladas(rulings, sat=None, office=None) -> list:
    """06/11: ativações que o dono ouviu e confirmou (o Whisper não reconheceu a palavra). Saem com prefixo `v:`."""
    return ["v:" + n for n in _validados(rulings, "verdadeiras_ativacoes", sat, office)]


def _validados(rulings, chave, sat, office):
    """18/11/2026: o nome pode vir como `<sat_id>__<arquivo>`; sem prefixo, é do satélite do escritório (`office`).
    Com `sat`, ficam só os nomes desse satélite, sem o prefixo (é o nome do arquivo na Pi dele)."""
    out = []
    for n in sorted(rulings.get(chave, [])):
        m = re.fullmatch(r"(?:(sat-[0-9a-f]{6})__)?((?:sexta_feira_)?\d{8}_\d{6}\.wav)", n)
        if not m:
            raise ValueError(f"nome de ativação inválido em {chave}: {n!r}")
        if sat is None or (m.group(1) or office) == sat:
            out.append(m.group(2))
    return sorted(out)
