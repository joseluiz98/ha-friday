"""O aviso falado de um timer com nome (spec 2026-11-24, objetivo 3): ao criar o timer o bot sintetiza "O timer <nome>
terminou." com o mesmo TTS e a mesma voz dos anúncios (cache incluído) e manda o WAV ao satélite, que o guarda até o
timer acabar ou ser cancelado. No vencimento o satélite não precisa de rede. Falhar aqui nunca atrapalha o timer: o
satélite cai no aviso genérico que já tem. Cada falha de TTS entra na contagem de saúde (tts_saude)."""
import asyncio
import logging
import re
import time

import anuncios as an
import tts_saude

TEXTO_AVISO_TIMER = "O timer {nome} terminou."
_ID_TIMER = re.compile(r"^t[0-9a-f]{6}$")


def timer_criado(resposta):
    """(id, nome|None) de uma resposta `ok` do `timer_criar` da Pi; None para qualquer outra coisa."""
    if not isinstance(resposta, dict) or not resposta.get("ok") or not isinstance(resposta.get("timer"), dict):
        return None
    t = resposta["timer"]
    tid, nome = t.get("id"), t.get("nome")
    if not isinstance(tid, str) or not _ID_TIMER.match(tid):
        return None
    return tid, (nome.strip() or None) if isinstance(nome, str) else None


async def _enviar(deps, sid: str, timer_id: str, wav: bytes) -> str:
    """IP do cabo e, se ele não responder, o do Wi-Fi (como o anúncio). "ok", "sem_timer" ou "envio_falhou"."""
    sat = deps.satelites.get(sid)
    if not sat:
        return "envio_falhou"
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
            r = await deps.enviar_voz_timer(ip, sat["senha"], timer_id, wav)
        except Exception:
            continue
        if r in ("ok", "sem_timer"):  # a Pi respondeu: "sem_timer" = acabou ou foi cancelado, não adianta insistir
            return r
    return "envio_falhou"


async def avisar_voz_do_timer(deps, sid: str, timer_id: str, nome) -> str:
    """"ok", "sem_nome", "tts_falhou", "sem_timer" ou "envio_falhou". Nunca levanta."""
    linha = {"tipo": "timer_voz", "sid": sid, "timer": timer_id, "cache": None, "tts_ms": 0}
    try:
        if not nome:
            linha["resultado"] = "sem_nome"
            return "sem_nome"
        tts = deps.ajustes_tts()
        texto = TEXTO_AVISO_TIMER.format(nome=nome)
        k = an.chave(tts, texto)
        wav = deps.cache.ler(k)
        linha["cache"] = "hit" if wav is not None else "miss"
        if wav is None:
            t0 = time.monotonic()
            try:
                pcm, uso = await asyncio.wait_for(deps.tts(tts, texto), getattr(deps, "tts_timeout_s", an.TTS_TIMEOUT_S))
            except Exception as e:
                linha.update(resultado="tts_falhou", tts_erro=type(e).__name__,
                             tts_ms=round((time.monotonic() - t0) * 1000))
                tts_saude.registrar(False, "timer", type(e).__name__)
                return "tts_falhou"
            tts_saude.registrar(True, "timer")
            linha["tts_ms"] = round((time.monotonic() - t0) * 1000)
            wav = an.wav_48k_estereo(pcm)
            deps.cache.gravar(k, wav)
            try:
                deps.registrar_uso(f"timer-{timer_id}", tts["model"], uso)
            except Exception:
                logging.warning("timer_voz: could not write the usage", exc_info=True)
        resultado = await _enviar(deps, sid, timer_id, wav)
        linha["resultado"] = resultado
        return resultado
    except Exception:  # nada daqui pode atrapalhar o timer
        logging.warning("timer_voz: unexpected failure", exc_info=True)
        linha["resultado"] = "erro"
        return "envio_falhou"
    finally:
        try:
            deps.registrar(linha)
        except Exception:
            pass
