"""Vigia do Gemini Live depois de um resultado do cérebro (19/10, caso real 13:59).

O cérebro devolveu em 7 s, o bot entregou o texto ao Gemini e ele não falou nada em 8 s: a Pi fechou por
"cerebro_concluido" e não havia registro do que o Gemini mandou (nada, turn_complete vazio, interrupção?).
Durante WINDOW_S depois da entrega, ou até ela começar a falar, cada mensagem do Gemini vai resumida ao log.
Lógica pura, sem pipecat: testada em test_gemini_watch.py.
"""
import time

WINDOW_S = 15.0
SILENCE_WARN_S = 8.0  # igual ao cerebro_fim_s da Pi: depois disso a conversa fecha calada
_TEXTO_MAX = 200


def summarize_server_message(msg) -> dict:
    """Campos presentes numa LiveServerMessage, sem bytes de áudio."""
    out = {}
    sc = getattr(msg, "server_content", None)
    if sc is not None:
        mt = getattr(sc, "model_turn", None)
        parts = list(getattr(mt, "parts", None) or []) if mt is not None else []
        if parts:
            out["parts"] = len(parts)
            audio = sum(1 for p in parts if getattr(p, "inline_data", None) is not None)
            if audio:
                out["audio_parts"] = audio
            textos = [p.text for p in parts if getattr(p, "text", None)]
            if textos:
                out["text"] = " ".join(textos)[:_TEXTO_MAX]
        ot = getattr(sc, "output_transcription", None)
        if ot is not None and getattr(ot, "text", None):
            out["said"] = ot.text[:_TEXTO_MAX]
        for campo in ("turn_complete", "interrupted", "generation_complete", "waiting_for_input"):
            if getattr(sc, campo, None):
                out[campo] = True
        status = getattr(sc, "interaction_status", None)
        if status is not None:
            out["interaction_status"] = str(status)
    tc = getattr(msg, "tool_call", None)
    if tc is not None:
        out["tool_call"] = [getattr(fc, "name", None) for fc in (getattr(tc, "function_calls", None) or [])]
    for campo in ("tool_call_cancellation", "go_away"):
        if getattr(msg, campo, None) is not None:
            out[campo] = True
    if getattr(msg, "usage_metadata", None) is not None:
        out["usage"] = True
    return out or {"other": True}


class ResultWatch:
    """Janela aberta na entrega de um resultado do cérebro; fecha quando ela começa a falar ou em WINDOW_S."""

    def __init__(self, clock=time.monotonic):
        self._clock = clock
        self._opened_at = None

    def open(self):
        self._opened_at = self._clock()

    def elapsed(self) -> float:
        return self._clock() - self._opened_at if self._opened_at is not None else 0.0

    def active(self) -> bool:
        return self._opened_at is not None and self.elapsed() < WINDOW_S

    def speech_started(self):
        """Fecha a janela; devolve quantos segundos ela levou para começar a falar (None se não estava aberta)."""
        if self._opened_at is None:
            return None
        latencia = self.elapsed()
        self._opened_at = None
        return latencia

    def silent_too_long(self) -> bool:
        return self._opened_at is not None and self.elapsed() >= SILENCE_WARN_S
