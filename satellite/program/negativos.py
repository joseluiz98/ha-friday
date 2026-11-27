"""Negativos do treino do verificador (spec 2026-11-23-voice-training-baseline-negatives). Sem openWakeWord e sem rede:
só escolhe arquivos e decide se há negativos suficientes. O treino (treino_verificador.py) e o agente local usam isto.

Fontes, nesta ordem de preferência:
1. o ambiente gravado pelo próprio satélite (mesmo microfone, mesma sala): vale sozinho a partir de MIN_AMBIENTE_S;
2. senão, o ambiente próprio que houver MAIS a linha de base: negativos dos outros satélites que a VM conhece no
   momento do treino (o agente local baixa em RAIZ/_baseline). Satélite novo treina no primeiro dia.
`_negativos/` (acervo de 15/10, só no MK1) continua valendo quando existe."""
import glob, json, os, re

RAIZ = "/home/jose/vozes"
AMBIENTE = "/home/jose/gravacoes/ambiente"
BASELINE = f"{RAIZ}/_baseline"
TAXA, BYTES_AMOSTRA, CABECALHO = 16000, 2, 44
MIN_AMBIENTE_S = 1800      # ambiente próprio suficiente para dispensar a linha de base (30 min)
ALVO_NEGATIVOS_S = 900     # quanto de ambiente entra no treino (15 min: a Pi 3 extrai ~1 min por clipe de 5 min)
MIN_NEGATIVOS_S = 300      # abaixo disto o portão não aprova: não há como provar que barra o que não é a palavra
_DIA = re.compile(r"(\d{8})")


def segundos(caminho) -> float:
    """Duração de um WAV 16 kHz mono pelo tamanho (sem abrir o arquivo)."""
    try:
        return max(os.path.getsize(caminho) - CABECALHO, 0) / (TAXA * BYTES_AMOSTRA)
    except OSError:
        return 0.0


def listar(pasta, padrao="*.wav") -> list:
    return sorted(glob.glob(os.path.join(pasta, padrao)))


def total_s(arquivos) -> float:
    return sum(segundos(a) for a in arquivos)


def espalhar(arquivos, alvo_s) -> list:
    """Clipes igualmente espaçados no tempo (nome ordenado = ordem cronológica) até somar `alvo_s`."""
    if not arquivos or alvo_s <= 0:
        return []
    media = max(total_s(arquivos) / len(arquivos), 1.0)
    n = min(len(arquivos), max(1, round(alvo_s / media)))
    passo = len(arquivos) / n
    return [arquivos[int(i * passo + passo / 2)] for i in range(n)]


def grupo(caminho, origem) -> str:
    """Grupo da validação cruzada: o dia e de onde veio. Clipes do mesmo dia e lugar caem do mesmo lado."""
    m = _DIA.search(os.path.basename(caminho))
    return f"n:{origem}:{m.group(1) if m else os.path.basename(caminho)}"


def escolher(ambiente=AMBIENTE, baseline=BASELINE, acervo=f"{RAIZ}/_negativos") -> dict:
    """{"clipes": [(caminho, grupo)], "origem", "proprio_s", "baseline_s", "acervo_s", "total_s"}.
    origem: "ambiente" (só o próprio), "misto" (próprio + linha de base), "baseline" (só a linha de base),
    "nenhuma"."""
    proprio = listar(ambiente)
    proprio_s = total_s(proprio)
    base = listar(baseline)
    escolhidos = espalhar(proprio, ALVO_NEGATIVOS_S)
    clipes = [(c, grupo(c, "p")) for c in escolhidos]
    falta = max(ALVO_NEGATIVOS_S - total_s(escolhidos), 0)
    usados = []
    if proprio_s < MIN_AMBIENTE_S:
        # arquivos `<sat>__falsa_*` são negativos duros dos outros satélites (disparos falsos): entram todos;
        # os `<sat>__ambiente_*` completam o que faltou, igualmente espaçados
        duros = [b for b in base if "__falsa_" in os.path.basename(b)]
        ambientes = [b for b in base if "__falsa_" not in os.path.basename(b)]
        usados = duros + espalhar(ambientes, falta)
        clipes += [(b, grupo(b, "b:" + os.path.basename(b).split("__", 1)[0])) for b in usados]
    baseline_s = total_s(usados)
    ac_s = total_s(listar(acervo))
    origem = ("ambiente" if usados == [] and escolhidos else "misto" if usados and escolhidos
              else "baseline" if usados else "nenhuma")
    return {"clipes": clipes, "origem": origem, "proprio_s": round(total_s(escolhidos), 1),
            "baseline_s": round(baseline_s, 1), "acervo_s": round(ac_s, 1),
            "total_s": round(total_s(escolhidos) + baseline_s + ac_s, 1)}


def veredito(neg) -> str | None:
    """None quando há negativos suficientes; senão o motivo (vai por voz e por Telegram, então diz o que fazer)."""
    if neg["total_s"] >= MIN_NEGATIVOS_S:
        return None
    return (f"faltam negativos para provar o verificador: há {round(neg['total_s'] / 60, 1)} min de som sem a palavra "
            f"e preciso de pelo menos {MIN_NEGATIVOS_S // 60}. O satélite grava o ambiente sozinho e o servidor "
            "empresta o de outros satélites; tente de novo quando algum dos dois existir")


def carregar_rotulos(caminho=f"{RAIZ}/rotulos.json") -> dict:
    """O acervo de 15/10 só existe no MK1: sem o arquivo, o satélite treina com rótulos vazios (não é erro)."""
    try:
        with open(caminho, encoding="utf-8") as f:
            r = json.load(f)
    except (OSError, ValueError):
        return {"verdadeiras": [], "falsas": []}
    return r if isinstance(r, dict) else {"verdadeiras": [], "falsas": []}
