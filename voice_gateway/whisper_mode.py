"""Modo sussurro da voz (spec 2026-10-20 D7-D9): turnos dirigidos pela Pi e resposta pelo TTS sussurrado.

Conversa sussurrada: o Gemini Live roda sem a detecção de atividade do servidor (a Pi avisa início e fim da fala,
PiSpeechTurns) e o áudio dele é trocado, frase a frase, pelo do TTS `gemini-3.8-flash-tts` com `style: whispers`
(WhisperVoice). Se o TTS falha ou demora, o resto do turno sai pelo áudio do próprio Live, mais baixo.
"""
import asyncio
import re
import time

import numpy as np
from loguru import logger
from pipecat.frames.frames import (
    Frame,
    InputAudioRawFrame,
    InterruptionFrame,
    OutputAudioRawFrame,
    TTSAudioRawFrame,
    TTSStoppedFrame,
    TTSTextFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

TTS_RATE = 24000
CHUNK_BYTES = TTS_RATE * 2 // 50  # 20 ms de PCM 16 bits mono
_FIM_DE_FRASE = re.compile(r"[.!?…]+\s")


def split_sentences(buffer: str):
    """Corta o texto depois de `.`, `!`, `?` ou `…` seguidos de espaço. Devolve (frases completas, resto)."""
    frases, ini = [], 0
    for m in _FIM_DE_FRASE.finditer(buffer):
        frase = buffer[ini:m.end()].strip()
        if frase:
            frases.append(frase)
        ini = m.end()
    return frases, buffer[ini:]


def _rate_do_mime(mime: str, padrao: int = TTS_RATE) -> int:
    m = re.search(r"rate=(\d+)", mime or "")
    return int(m.group(1)) if m else padrao


def _reamostrar(pcm: bytes, de: int, para: int = TTS_RATE) -> bytes:
    if de == para or not pcm:
        return pcm
    x = np.frombuffer(pcm, dtype=np.int16).astype(np.float32)
    n = max(1, int(round(len(x) * para / de)))
    y = np.interp(np.linspace(0, len(x) - 1, n), np.arange(len(x)), x)
    return np.clip(y, -32768, 32767).astype(np.int16).tobytes()


async def stream_whisper_tts(client, model: str, voice: str, text: str):
    """PCM 24 kHz mono S16LE da frase sussurrada, em pedaços, conforme o TTS entrega (1º áudio em ~0,8 s, P4)."""
    from google.genai import types
    stream = await client.aio.models.generate_content_stream(
        model=model,
        contents=types.Content(role="user", parts=[types.Part(
            text=text, speech_metadata=types.SpeechMetadata(style="whispers"))]),
        config=types.GenerateContentConfig(
            response_modalities=["AUDIO"],
            speech_config=types.SpeechConfig(voice_config=types.VoiceConfig(
                prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=voice)))))
    sobra = b""
    async for resp in stream:
        for cand in (getattr(resp, "candidates", None) or []):
            for part in (getattr(getattr(cand, "content", None), "parts", None) or []):
                blob = getattr(part, "inline_data", None)
                if not blob or not blob.data:
                    continue
                dados = sobra + blob.data
                sobra = dados[len(dados) // 2 * 2:]
                dados = dados[:len(dados) // 2 * 2]
                if dados:
                    yield _reamostrar(dados, _rate_do_mime(blob.mime_type))


class PiSpeechTurns(FrameProcessor):
    """D7: transforma `speech_started`/`speech_stopped` da Pi em UserStarted/StoppedSpeakingFrame, para baixo, rumo ao
    Gemini Live (que então manda activity_start/activity_end). Idempotente. Com `ready`, espera o Live ficar pronto
    (antes disso ele não manda activity_start e o áudio ficaria sem janela)."""

    def __init__(self, ready=None, ready_timeout_s=10.0, min_audio_s=0.2, stop_timeout_s=3.0):
        super().__init__()
        self._ready, self._timeout = ready or (lambda: True), ready_timeout_s
        self._falando = False
        self._cadeia = None
        # 21/10 (reproduzido na bancada): activity_start + activity_end sem áudio entre eles = 1007 "Precondition check
        # failed" e a sessão do Gemini morre. O fim só sai depois de `min_audio_s` de áudio dentro da janela.
        self._min_bytes = int(16000 * 2 * min_audio_s)
        self._stop_timeout = stop_timeout_s
        self._bytes_na_janela = 0
        self._fim_pendente = None  # tarefa do prazo de segurança

    async def _emitir(self, falando: bool):
        if not falando and self._falando and self._bytes_na_janela < self._min_bytes:
            if self._fim_pendente is None:
                self._fim_pendente = asyncio.get_running_loop().create_task(self._fim_por_prazo())
            return
        await self._trocar(falando)

    async def _trocar(self, falando: bool):
        if self._fim_pendente is not None and not falando:
            tarefa, self._fim_pendente = self._fim_pendente, None
            if tarefa is not asyncio.current_task():
                tarefa.cancel()
        if falando and self._fim_pendente is not None:  # falou de novo antes de o fim sair: o fim some
            self._fim_pendente.cancel()
            self._fim_pendente = None
        if falando == self._falando:
            return
        self._falando = falando
        if falando:
            self._bytes_na_janela = 0
        await self.push_frame(UserStartedSpeakingFrame() if falando else UserStoppedSpeakingFrame(),
                              FrameDirection.DOWNSTREAM)

    async def _fim_por_prazo(self):
        await asyncio.sleep(self._stop_timeout)
        logger.warning("whisper: fim da fala sem áudio na janela; enviando assim mesmo")
        await self._trocar(False)

    async def _depois_de_pronto(self, anterior, falando):
        if anterior is not None:
            await asyncio.gather(anterior, return_exceptions=True)
        fim = time.monotonic() + self._timeout
        while not self._ready() and time.monotonic() < fim:
            await asyncio.sleep(0.05)
        await self._emitir(falando)

    async def _pedir(self, falando: bool):
        pendente = self._cadeia is not None and not self._cadeia.done()
        if self._ready() and not pendente:
            await self._emitir(falando)
            return
        self._cadeia = asyncio.get_running_loop().create_task(
            self._depois_de_pronto(self._cadeia if pendente else None, falando))

    async def started(self):
        await self._pedir(True)

    async def stopped(self):
        await self._pedir(False)

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        await self.push_frame(frame, direction)
        if isinstance(frame, InputAudioRawFrame) and direction == FrameDirection.DOWNSTREAM and self._falando:
            self._bytes_na_janela += len(frame.audio)
            if self._fim_pendente is not None and self._bytes_na_janela >= self._min_bytes:
                await self._trocar(False)  # depois do áudio: o Live recebe o áudio antes do activity_end


class _Turno:
    def __init__(self, anterior):
        self.live = bytearray()   # áudio do Live deste turno (descartado, salvo na reserva)
        self.recebido = 0         # caracteres de transcrição recebidos
        self.enfileirado = 0      # caracteres cobertos pelas frases já enfileiradas
        self.falado = 0           # caracteres cobertos pelo que o TTS já falou
        self.pendente = ""
        self.fila = asyncio.Queue()
        self.fim = None           # TTSStoppedFrame segurado até a fila esvaziar
        self.reserva = False
        self.tarefa = None
        self.anterior = anterior  # tarefa do turno anterior: os áudios nunca se misturam
        self.frases_ok = 0
        self.marcou = False       # já avisou o transporte de que ela está falando (revisão 20/10)


class WhisperVoice(FrameProcessor):
    """D8/D9: troca o áudio do Live pelo TTS sussurrado, frase a frase, com reserva no áudio do Live.
    `tts(texto)` devolve um iterador assíncrono de PCM 24 kHz mono S16LE."""

    def __init__(self, tts, first_audio_timeout_s=3.0, fallback_gain_db=-12.0):
        super().__init__()
        self._tts, self._timeout = tts, first_audio_timeout_s
        self._ganho = 10 ** (fallback_gain_db / 20.0)
        self._lock = asyncio.Lock()
        self._turno = _Turno(None)
        self._tarefas = []
        self._fins = []  # TTSStoppedFrame segurados

    # ---- saída
    def _com_ganho(self, pcm: bytes) -> bytes:
        x = np.frombuffer(pcm[:len(pcm) // 2 * 2], dtype=np.int16).astype(np.float32) * self._ganho
        return np.clip(np.rint(x), -32768, 32767).astype(np.int16).tobytes()

    async def _empurrar_audio(self, pcm, rate=TTS_RATE, canais=1):
        for i in range(0, len(pcm), CHUNK_BYTES):
            # TTSAudioRawFrame (revisão 20/10): só com ele o transporte vê o bot falando (BotStarted/StoppedSpeaking →
            # `ela_comecou` na Pi e o turno no Logbook); o fim vem do TTSStoppedFrame, não das pausas entre frases
            await self.push_frame(TTSAudioRawFrame(audio=pcm[i:i + CHUNK_BYTES], sample_rate=rate,
                                                   num_channels=canais), FrameDirection.DOWNSTREAM)

    async def _entrar_na_reserva(self, t, motivo):
        """Com o lock: o que o TTS ainda não falou sai pelo áudio do Live, com ganho menor."""
        if t.reserva:
            return
        t.reserva = True
        logger.warning(f"whisper: reserva ({motivo})")
        total = len(t.live)
        corte = int(total * t.falado / t.recebido) // 2 * 2 if t.recebido else 0
        resto = bytes(t.live[min(corte, total):])
        t.live = bytearray()
        if resto:
            await self._empurrar_audio(self._com_ganho(resto))

    # ---- tarefa do turno
    async def _falar_frase(self, t, n, frase, cobertura):
        t0 = time.monotonic()
        agen = self._tts(frase)
        try:
            try:
                pedaco = await asyncio.wait_for(agen.__anext__(), timeout=self._timeout)
            except StopAsyncIteration:
                raise RuntimeError("o TTS não devolveu áudio") from None
            logger.info(f"whisper: frase {n} 1º áudio {int((time.monotonic() - t0) * 1000)} ms")
            primeiro = True
            while True:
                async with self._lock:
                    if t.reserva:
                        return
                    await self._empurrar_audio(pedaco)
                if primeiro:
                    t.falado, primeiro = cobertura, False  # a parte falada nunca se repete na reserva
                try:
                    pedaco = await asyncio.wait_for(agen.__anext__(), timeout=self._timeout * 3)
                except StopAsyncIteration:
                    break
        finally:
            aclose = getattr(agen, "aclose", None)
            if aclose:
                try:
                    await aclose()
                except Exception:  # noqa: BLE001
                    pass

    async def _trabalhar(self, t):
        if t.anterior is not None:
            await asyncio.gather(t.anterior, return_exceptions=True)
        n = 0
        while True:
            item = await t.fila.get()
            if item is None:
                break
            if t.reserva:
                continue
            n += 1
            try:
                await self._falar_frase(t, n, *item)
                t.frases_ok += 1
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                motivo = "1º áudio lento" if isinstance(e, asyncio.TimeoutError) else f"{type(e).__name__}: {e}"
                async with self._lock:
                    await self._entrar_na_reserva(t, motivo)
        if not t.reserva and t.frases_ok == 0 and t.live:  # o Live falou sem texto para o TTS: melhor baixo que mudo
            async with self._lock:
                await self._entrar_na_reserva(t, "sem transcrição")
        if t.fim is not None:
            fim, t.fim = t.fim, None
            if fim in self._fins:
                self._fins.remove(fim)
            await self.push_frame(fim, FrameDirection.DOWNSTREAM)

    def _iniciar(self, t):
        if t.tarefa is None:
            self._tarefas = [x for x in self._tarefas if not x.done()]
            t.tarefa = asyncio.get_running_loop().create_task(self._trabalhar(t))
            self._tarefas.append(t.tarefa)

    def _enfileirar(self, t, frases, cobertura):
        for i, f in enumerate(frases):
            t.enfileirado = cobertura if i == len(frases) - 1 else t.enfileirado + len(f) + 1
            t.fila.put_nowait((f, t.enfileirado))
        if frases:
            self._iniciar(t)

    # ---- quadros
    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if direction != FrameDirection.DOWNSTREAM:
            await self.push_frame(frame, direction)
            return
        t = self._turno
        if isinstance(frame, OutputAudioRawFrame):
            if not t.marcou:
                # revisão 20/10: o Live começou a responder; marca a fala já (20 ms de silêncio), como no modo normal,
                # para a espera curta da Pi não correr enquanto o TTS alcança o Live (19:18: cortou a previsão)
                t.marcou = True
                async with self._lock:
                    await self._empurrar_audio(bytes(CHUNK_BYTES))
            if t.reserva:
                async with self._lock:
                    await self._empurrar_audio(self._com_ganho(frame.audio), frame.sample_rate, frame.num_channels)
            else:
                t.live.extend(frame.audio)
        elif isinstance(frame, TTSTextFrame):
            await self.push_frame(frame, direction)
            texto = frame.text or ""
            t.recebido += len(texto)
            frases, t.pendente = split_sentences(t.pendente + texto)
            self._enfileirar(t, frases, t.recebido - len(t.pendente))
        elif isinstance(frame, TTSStoppedFrame):
            resto, t.pendente = t.pendente.strip(), ""
            if resto:
                self._enfileirar(t, [resto], t.recebido)
            t.fim = frame
            self._fins.append(frame)
            self._iniciar(t)
            t.fila.put_nowait(None)
            self._turno = _Turno(t.tarefa)
        elif isinstance(frame, InterruptionFrame):
            await self._cancelar()
            await self.push_frame(frame, direction)
        else:
            await self.push_frame(frame, direction)

    async def _cancelar(self):
        """A pessoa falou por cima: nada mais do TTS sai; os quadros de fim de turno segurados seguem."""
        tarefas, self._tarefas = self._tarefas, []
        for tarefa in tarefas:
            tarefa.cancel()
        if tarefas:
            await asyncio.gather(*tarefas, return_exceptions=True)
        fins, self._fins = self._fins, []
        self._turno = _Turno(None)
        for fim in fins:
            await self.push_frame(fim, FrameDirection.DOWNSTREAM)
