"""Saúde do TTS (spec 2026-11-24, objetivo 5): conta as falhas de TTS SEGUIDAS, sem sucesso no meio e em qualquer
intervalo de tempo (timer é raro: uma janela de minutos quase nunca dispararia). Timers e anúncios usam a mesma
chamada de TTS, então dividem a sequência. A 3ª falha seguida liga o problema; o próximo TTS que dá certo desliga; sem
nenhuma tentativa por 7 dias a sequência expira sozinha. O agente do servidor lê este arquivo e mostra o sensor no HA.
Nunca levanta: registrar a saúde não pode atrapalhar um timer nem um anúncio."""
import json, logging, os, time

LIMITE_SEGUIDAS = 3
EXPIRA_S = 7 * 86400
PADRAO = "/srv/friday/state/tts-health.json"
_VAZIO = {"seguidas": 0, "desde": None, "ultimo_erro": None, "origem": None, "ultima_tentativa": None}


def _arquivo() -> str:
    return os.environ.get("FRIDAY_TTS_HEALTH", PADRAO)


def _ler() -> dict:
    try:
        with open(_arquivo()) as f:
            d = json.load(f)
        return dict(_VAZIO, **{k: d[k] for k in _VAZIO if k in d}) if isinstance(d, dict) else dict(_VAZIO)
    except (OSError, ValueError):
        return dict(_VAZIO)


def _gravar(d: dict) -> None:
    arq = _arquivo()
    try:
        os.makedirs(os.path.dirname(arq), exist_ok=True)
        with open(arq + ".tmp", "w") as f:
            json.dump(d, f)
        os.replace(arq + ".tmp", arq)
    except OSError:
        logging.warning("tts_saude: não consegui gravar %s", arq, exc_info=True)


def registrar(ok: bool, origem: str, erro: str | None = None, agora: float | None = None) -> None:
    """Uma tentativa de TTS. `origem` = "timer" ou "anuncio"."""
    agora = time.time() if agora is None else agora
    d = _ler()
    if d["ultima_tentativa"] is not None and agora - d["ultima_tentativa"] > EXPIRA_S:
        d = dict(_VAZIO)  # a sequência velha expirou: esta tentativa começa outra
    if ok:
        d = dict(_VAZIO, ultima_tentativa=agora)
    else:
        d["seguidas"] += 1
        if d["seguidas"] == 1:
            d["desde"] = agora
        d.update(ultimo_erro=erro, origem=origem, ultima_tentativa=agora)
    _gravar(d)


def estado(agora: float | None = None) -> dict:
    agora = time.time() if agora is None else agora
    d = _ler()
    vivo = d["ultima_tentativa"] is not None and agora - d["ultima_tentativa"] <= EXPIRA_S
    return {**d, "falhando": bool(vivo and d["seguidas"] >= LIMITE_SEGUIDAS)}
