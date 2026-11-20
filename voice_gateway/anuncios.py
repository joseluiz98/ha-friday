"""Anúncios nos satélites (spec 2026-11-20 satellite announcements, Flow 4): POST /announce do bot.

Texto fixo → TTS do Gemini (voz, modelo e ritmo da fonte única `voice_settings.tts`) → WAV 48 kHz estéreo (cache por
sha256 de voz+modelo+ritmo+texto) → todos os satélites pedidos, em paralelo, no IP do cabo e depois no do Wi-Fi.
Quem não responde (conexão recusada/prazo) fica `reenviando` e o bot tenta de novo a cada 15 s por até 300 s.
Lógica com IO injetado (deps): roda sem Pipecat e sem rede (test_anuncios.py); quem liga isto ao servidor é o bot.py."""
import asyncio
import hashlib
import io
import logging
import os
import secrets
import tempfile
import time
import wave

MAX_TEXTO = 500
TTS_TIMEOUT_S = 15.0
RETRY_CADA_S = 15.0
RETRY_ATE_S = 300.0
CACHE_LIMITE = 200
RESPOSTAS_DA_PI = ("vou_tocar", "na_fila", "calado")

_em_curso: set = set()  # referência às tarefas de reenvio (o laço só guarda referência fraca)


def validar(corpo, satelites: dict) -> tuple[str, list[str]]:
    """(texto, ids). `satelites` ausente = todos os cadastrados. Levanta ValueError."""
    if not isinstance(corpo, dict):
        raise ValueError("corpo precisa ser um objeto")
    texto = corpo.get("text")
    texto = texto.strip() if isinstance(texto, str) else ""
    if not 1 <= len(texto) <= MAX_TEXTO:
        raise ValueError(f"texto de 1 a {MAX_TEXTO} caracteres")
    ids = corpo.get("satellites")
    if ids is None:
        ids = sorted(satelites)
    elif not isinstance(ids, list) or not ids or not all(isinstance(i, str) for i in ids):
        raise ValueError("satellites é uma lista de ids")
    ids = list(dict.fromkeys(ids))
    desconhecidos = [i for i in ids if i not in satelites]
    if desconhecidos:
        raise ValueError("satélite desconhecido")
    if not ids:
        raise ValueError("nenhum satélite cadastrado")
    return texto, ids


class Cache:
    """WAVs prontos em disco, um arquivo por chave; passou de `limite`, sai o de mtime mais antigo. Ler renova o mtime."""

    def __init__(self, pasta: str, limite: int = CACHE_LIMITE):
        self.pasta, self.limite = pasta, limite

    def _arq(self, chave: str) -> str:
        return os.path.join(self.pasta, f"{chave}.wav")

    def ler(self, chave: str) -> bytes | None:
        try:
            with open(self._arq(chave), "rb") as f:
                dados = f.read()
            os.utime(self._arq(chave))
            return dados
        except OSError:
            return None

    def gravar(self, chave: str, wav: bytes) -> None:
        os.makedirs(self.pasta, mode=0o700, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.pasta, suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(wav)
            os.replace(tmp, self._arq(chave))
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        arqs = []
        for nome in os.listdir(self.pasta):
            if nome.endswith(".wav"):
                p = os.path.join(self.pasta, nome)
                try:
                    arqs.append((os.stat(p).st_mtime, p))
                except OSError:
                    pass
        arqs.sort()
        for _, p in arqs[:max(0, len(arqs) - self.limite)]:
            try:
                os.unlink(p)
            except OSError:
                pass


def chave(tts: dict, texto: str) -> str:
    return hashlib.sha256(f"{tts['voice']}\n{tts['model']}\n{tts.get('rate', 'normal')}\n{texto}".encode()).hexdigest()


def wav_48k_estereo(pcm24_mono: bytes) -> bytes:
    import numpy as np
    from whisper_mode import _reamostrar
    pcm = pcm24_mono[:len(pcm24_mono) // 2 * 2]
    mono = np.frombuffer(_reamostrar(pcm, 24000, 48000), dtype=np.int16)
    estereo = np.repeat(mono[:, None], 2, axis=1)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(48000)
        w.writeframes(estereo.tobytes())
    return buf.getvalue()


def _tokens_da_resposta(resp) -> dict:
    import usage_ledger as ul
    u = getattr(resp, "usage_metadata", None)

    def audio(detalhes):
        total = 0
        for d in (detalhes or []):
            if "AUDIO" in str(getattr(d, "modality", "")).upper():
                total += getattr(d, "token_count", 0) or 0
        return total

    return ul.voice_tokens(getattr(u, "prompt_token_count", 0), getattr(u, "candidates_token_count", 0),
                           getattr(u, "cached_content_token_count", 0), getattr(u, "thoughts_token_count", 0),
                           audio(getattr(u, "prompt_tokens_details", None)),
                           audio(getattr(u, "candidates_tokens_details", None)),
                           audio(getattr(u, "cache_tokens_details", None)))


async def falar(client, tts: dict, texto: str) -> tuple[bytes, dict]:
    """TTS não-streaming: (PCM 24 kHz mono S16LE, tokens para o registro de custo). Sem estilo; o ritmo vai no texto."""
    from google.genai import types
    import voice_settings
    from whisper_mode import _rate_do_mime, _reamostrar
    resp = await client.aio.models.generate_content(
        model=tts["model"],
        contents=voice_settings.tts_texto(texto, tts.get("rate", "normal")),
        config=types.GenerateContentConfig(
            response_modalities=["AUDIO"],
            speech_config=types.SpeechConfig(voice_config=types.VoiceConfig(
                prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=tts["voice"])))))
    pcm = b""
    for cand in (getattr(resp, "candidates", None) or []):
        for part in (getattr(getattr(cand, "content", None), "parts", None) or []):
            blob = getattr(part, "inline_data", None)
            if blob and blob.data:
                dados = blob.data[:len(blob.data) // 2 * 2]
                pcm += _reamostrar(dados, _rate_do_mime(blob.mime_type), 24000)
    if not pcm:
        raise RuntimeError("TTS sem áudio")
    return pcm, _tokens_da_resposta(resp)


async def _enviar_uma(deps, sid: str, anuncio_id: str, wav: bytes):
    """IP do cabo e, se ele não responder, o do Wi-Fi. Resposta da Pi, ou None se nenhum IP respondeu."""
    sat = deps.satelites[sid]
    tentados = []
    for origem in (lambda: sat.get("ip"), lambda: deps.ip_wifi(sid)):
        try:
            ip = origem()
            ip = await ip if asyncio.iscoroutine(ip) else ip
        except Exception:
            continue
        if not ip or ip in tentados:
            continue
        tentados.append(ip)
        try:
            return await deps.enviar(ip, sat["senha"], anuncio_id, wav)
        except Exception:
            continue
    return None


def _resposta_valida(r) -> str:
    return r if r in RESPOSTAS_DA_PI or r in ("sem_suporte", "erro") else "erro"


async def _reenviar(deps, sid: str, anuncio_id: str, wav: bytes, t0: float) -> None:
    resposta = "nao_entregue"
    try:
        while True:
            await deps.dormir(RETRY_CADA_S)
            if deps.agora() - t0 > RETRY_ATE_S:
                break
            r = await _enviar_uma(deps, sid, anuncio_id, wav)
            if r is not None:
                resposta = _resposta_valida(r)
                break
    except Exception as e:
        logging.warning(f"announce: retry for {sid} failed: {e!r}")
        resposta = "erro"
    deps.registrar({"tipo": "anuncio_final", "id": anuncio_id, "satelite": sid, "resposta": resposta,
                    "fim_em": round(deps.agora(), 1)})


async def anunciar(corpo, deps) -> dict:
    texto, ids = validar(corpo, deps.satelites)  # ValueError → 400, sem TTS
    t0 = deps.agora()
    anuncio_id = secrets.token_hex(6)
    ajustes = deps.ajustes_tts()  # lido a cada anúncio: o que o HA muda vale sem reiniciar
    k = chave(ajustes, texto)
    linha = {"tipo": "anuncio", "id": anuncio_id, "pedido_em": round(t0, 1), "chars": len(texto), "voz": ajustes["voice"],
             "ritmo": ajustes.get("rate", "normal")}
    wav = deps.cache.ler(k)
    linha["cache"] = "hit" if wav else "miss"
    if wav is None:
        t_tts = time.monotonic()
        try:
            pcm, uso = await asyncio.wait_for(deps.tts(ajustes, texto), getattr(deps, "tts_timeout_s", TTS_TIMEOUT_S))
            wav = wav_48k_estereo(pcm)
            deps.cache.gravar(k, wav)
        except Exception as e:
            linha.update(tts_erro=type(e).__name__, tts_ms=round((time.monotonic() - t_tts) * 1000),
                         satelites={sid: "erro" for sid in ids})
            deps.registrar(linha)
            return {"id": anuncio_id, "satelites": linha["satelites"], "cache": "miss"}
        linha["tts_ms"] = round((time.monotonic() - t_tts) * 1000)
        try:
            deps.registrar_uso(anuncio_id, ajustes["model"], uso)
        except Exception:
            logging.warning("announce: could not write the usage", exc_info=True)

    async def uma(sid):
        try:
            return await _enviar_uma(deps, sid, anuncio_id, wav)
        except Exception:
            return None

    respostas = await asyncio.gather(*(uma(sid) for sid in ids))
    resultado = {}
    for sid, r in zip(ids, respostas):
        if r is None:
            resultado[sid] = "reenviando"
            t = asyncio.create_task(_reenviar(deps, sid, anuncio_id, wav, t0))
            _em_curso.add(t)
            t.add_done_callback(_em_curso.discard)
        else:
            resultado[sid] = _resposta_valida(r)
    linha["satelites"] = resultado
    deps.registrar(linha)
    return {"id": anuncio_id, "satelites": resultado, "cache": linha["cache"]}


async def aguardar_pendentes() -> None:
    """Testes: espera os reenvios em andamento."""
    while _em_curso:
        await asyncio.gather(*list(_em_curso), return_exceptions=True)
