"""Funções puras da coleta (spec 2026-10-24): nome único, instante e eliminação de duplicatas por conteúdo."""
import datetime, os, re

# prioridade quando o MESMO áudio (mesmo sha1) aparece em mais de uma fonte: a primeira da lista vence (Foco 4)
PRIORIDADE = ("acervo_v", "acervo_f", "sessao", "ativou", "quase", "contexto", "barrada", "sem_voz", "fala_livre", "sombra")


def nome_unico(src: str, caminho: str) -> str:
    return f"{src}__{os.path.basename(caminho)}"


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


def deduplicar(linhas):
    """linhas: [(src, caminho, sha1)]. Devolve (fontes, descartadas): `fontes` = [{"src","path","name","ts","sha1"}]
    com um item por sha1 (fica a fonte de maior prioridade), na ordem de entrada; `descartadas` = quantas sobraram."""
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
            fontes.append({"src": src, "path": caminho, "name": nome_unico(src, caminho),
                           "ts": ts_do_nome(caminho), "sha1": sha})
            melhor[sha] = None  # o mesmo (src, caminho) repetido na entrada não duplica
    return fontes, len(linhas) - len(fontes)


def falsas_rotuladas(rulings) -> list:
    """Nomes das ativações (`ativacoes/AAAAMMDD_HHMMSS.wav`) que o dono ouviu e rotulou como falsas (31/10), lidos de
    `/share/wakeword-data/<modelo>/rulings.json` → "falsas_ativacoes". Só o nome do clipe da ativação: o nome vai na linha
    de comando do ssh e o contexto_ segue as regras próprias dele (os trechos sem a palavra já viram negativos)."""
    return _validados(rulings, "falsas_ativacoes")


def verdadeiras_rotuladas(rulings) -> list:
    """06/11: ativações que o dono ouviu e confirmou (o Whisper não reconheceu a palavra). Saem com prefixo `v:`."""
    return ["v:" + n for n in _validados(rulings, "verdadeiras_ativacoes")]


def _validados(rulings, chave):
    nomes = sorted(rulings.get(chave, []))
    for n in nomes:
        if not re.fullmatch(r"(sexta_feira_)?\d{8}_\d{6}\.wav", n):
            raise ValueError(f"nome de ativação inválido em {chave}: {n!r}")
    return nomes
