"""Configurações da voz escolhidas no Home Assistant (/srv/friday/state/voice-settings.json, Task 15).

O servidor da F.R.I.D.A.Y. (agente) grava o arquivo; o bot lê a cada conversa, sem cache e sem reinício.
O Gemini Live não tem parâmetro de velocidade de fala (só voz e idioma em speech_config), então velocidade
e modo breve viram FATOS no texto de abertura da voz (skill designing-for-friday: fato, não ordem)."""
import json
import os
import re

import local_config

ARQUIVO = os.environ.get("FRIDAY_VOICE_SETTINGS_FILE", "/srv/friday/state/voice-settings.json")
VOZES = {"Zephyr", "Puck", "Charon", "Kore", "Fenrir", "Leda", "Orus", "Aoede", "Callirrhoe", "Autonoe",
         "Enceladus", "Iapetus", "Umbriel", "Algieba", "Despina", "Erinome", "Algenib", "Rasalgethi",
         "Laomedeia", "Achernar", "Alnilam", "Schedar", "Gacrux", "Pulcherrima", "Achird",
         "Zubenelgenubi", "Vindemiatrix", "Sadachbia", "Sadaltager", "Sulafat"}
RITMOS = ("slow", "normal", "fast")
TTS_MODELO_PADRAO = "models/gemini-3.8-flash-tts"
# Ruling 20/11/2026: google-genai 2.34.0 (friday image) has no pace/rate field (SpeechMetadata = speaker + style,
# SpeechConfig = voice/language), so the Gemini TTS pace instruction is a prefix of the text.
_PREFIXO_RITMO = {"slow": "Fale devagar: ", "fast": "Fale rápido: "}
_MODELO = re.compile(r"(models/)?[A-Za-z0-9][A-Za-z0-9._-]{0,80}")


def ler(caminho=None) -> dict:
    """Só os campos válidos; arquivo ausente ou ilegível = {} (valem as variáveis de ambiente)."""
    try:
        with open(caminho or ARQUIVO, encoding="utf-8") as f:
            dados = json.load(f)
    except (OSError, ValueError):
        return {}
    if not isinstance(dados, dict):
        return {}
    saida = {}
    if dados.get("voice") in VOZES:
        saida["voice"] = dados["voice"]
    if isinstance(dados.get("model"), str) and _MODELO.fullmatch(dados["model"]):
        saida["model"] = dados["model"] if dados["model"].startswith("models/") else "models/" + dados["model"]
    if dados.get("speaking_rate") in RITMOS:
        saida["speaking_rate"] = dados["speaking_rate"]
    if isinstance(dados.get("brief_mode"), bool):
        saida["brief_mode"] = dados["brief_mode"]
    return saida


def fatos(cfg: dict) -> str:
    partes = []
    ritmo = cfg.get("speaking_rate")
    if ritmo == "slow":
        partes.append("A velocidade de fala que o " + local_config.owner() + " escolheu nas configurações para a sua voz é lenta.")
    elif ritmo == "fast":
        partes.append("A velocidade de fala que o " + local_config.owner() + " escolheu nas configurações para a sua voz é rápida.")
    if cfg.get("brief_mode"):
        partes.append("O modo breve está ligado nas configurações: o " + local_config.owner() + " escolheu respostas faladas curtas.")
    return " ".join(partes)


def voz(cfg=None, ambiente=None) -> str:
    """Nome da voz de TODA a conversa (sessão Live e TTS sussurrado): arquivo > FRIDAY_VOICE > Charon."""
    cfg = ler() if cfg is None else cfg
    ambiente = os.environ if ambiente is None else ambiente
    return cfg.get("voice") or ambiente.get("FRIDAY_VOICE") or "Charon"


def modelo(cfg=None, ambiente=None) -> str:
    cfg = ler() if cfg is None else cfg
    ambiente = os.environ if ambiente is None else ambiente
    return cfg.get("model") or ambiente.get("FRIDAY_GEMINI_MODEL") or "models/gemini-3.8-live"


def tts(cfg=None, ambiente=None) -> dict:
    """A identidade do TTS (anúncios e sussurro), numa fonte só: voz = a da conversa, modelo do TTS, ritmo das
    configurações. Trocar a voz no futuro = mudar aqui (ou o arquivo de configurações)."""
    cfg = ler() if cfg is None else cfg
    ambiente = os.environ if ambiente is None else ambiente
    return {"voice": voz(cfg, ambiente),
            "model": ambiente.get("FRIDAY_TTS_MODEL") or ambiente.get("FRIDAY_WHISPER_TTS_MODEL") or TTS_MODELO_PADRAO,
            "rate": cfg.get("speaking_rate") or "normal"}


def tts_texto(texto: str, rate: str) -> str:
    """O texto enviado ao TTS com o ritmo aplicado (`normal` = o texto como veio)."""
    return _PREFIXO_RITMO.get(rate, "") + texto
