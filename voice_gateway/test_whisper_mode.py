"""cd /srv/friday/app && /opt/pc/bin/python -m unittest test_whisper_mode -v
Spec 2026-10-20 D7-D9: turnos dirigidos pela Pi e voz sussurrada (TTS), com o Live e o TTS falsos."""
import asyncio
import unittest

import importlib.util

IN_CONTAINER = importlib.util.find_spec("pipecat") is not None  # pipecat only exists in the friday image
if IN_CONTAINER:
    import numpy as np
    from pipecat.clocks.system_clock import SystemClock
    from pipecat.frames.frames import (
        InputAudioRawFrame,
        InterruptionFrame,
        OutputAudioRawFrame,
        TTSAudioRawFrame,
        TTSStoppedFrame,
        TTSTextFrame,
        UserStartedSpeakingFrame,
        UserStoppedSpeakingFrame,
    )
    from pipecat.processors.frame_processor import FrameDirection, FrameProcessorSetup
    from pipecat.utils.asyncio.task_manager import TaskManager

    import whisper_mode as wm  # imports pipecat too

    D = FrameDirection.DOWNSTREAM


async def ligar(proc):
    """Processador solto, sem pipeline: um coletor no lugar do próximo da fila."""
    await proc.setup(FrameProcessorSetup(clock=SystemClock(), task_manager=TaskManager(), pipeline_worker=None))
    saida = []

    async def push(frame, direction=D):
        saida.append(frame)
    proc.push_frame = push
    return saida


def texto(t):
    f = TTSTextFrame(t, aggregated_by="sentence")
    f.includes_inter_frame_spaces = True
    return f


def audio(n, valor=1000):
    return OutputAudioRawFrame(audio=np.full(n // 2, valor, dtype=np.int16).tobytes(), sample_rate=24000,
                               num_channels=1)


def marcado(b):
    """PCM falso de uma frase: todas as amostras com o mesmo valor, para identificar de onde veio."""
    return np.full(480, b, dtype=np.int16).tobytes()


def tts_falso(por_frase, atraso=0.0, falha_em=None):
    async def tts(frase):
        if falha_em and frase in falha_em:
            raise RuntimeError("TTS fora")
        if atraso:
            await asyncio.sleep(atraso)
        yield por_frase[frase]
    return tts


def valores(saida):
    """Valor de cada quadro de áudio que saiu, sem o marcador de fala (silêncio) que o primeiro áudio do Live gera."""
    return [int(np.frombuffer(f.audio, dtype=np.int16)[0]) for f in saida
            if isinstance(f, OutputAudioRawFrame) and np.frombuffer(f.audio, dtype=np.int16).any()]


@unittest.skipUnless(IN_CONTAINER, "needs pipecat: runs in the friday image (scripts/test-all.sh)")
class TestSplit(unittest.TestCase):
    def test_corta_depois_da_pontuacao_com_espaco(self):
        self.assertEqual(wm.split_sentences("Oi. Tudo bem? Sim"), (["Oi.", "Tudo bem?"], "Sim"))

    def test_sem_espaco_depois_ainda_nao_fecha(self):
        self.assertEqual(wm.split_sentences("São 5h10."), ([], "São 5h10."))

    def test_vazio(self):
        self.assertEqual(wm.split_sentences(""), ([], ""))


@unittest.skipUnless(IN_CONTAINER, "needs pipecat: runs in the friday image (scripts/test-all.sh)")
class TestWhisperVoice(unittest.IsolatedAsyncioTestCase):
    async def test_audio_do_live_nao_passa(self):
        p = wm.WhisperVoice(tts_falso({}))
        saida = await ligar(p)
        await p.process_frame(audio(960), D)
        self.assertEqual(valores(saida), [])  # só o marcador de silêncio sai (TestMarcaDeFala)

    async def test_frases_viram_tts_em_ordem(self):
        p = wm.WhisperVoice(tts_falso({"Oi.": marcado(11), "Tudo bem?": marcado(22)}))
        saida = await ligar(p)
        parar = TTSStoppedFrame()
        for f in (texto("Oi. "), texto("Tudo bem? "), parar):
            await p.process_frame(f, D)
        await asyncio.sleep(0.2)
        self.assertEqual(valores(saida), [11, 22])
        self.assertIs(saida[-1], parar)
        self.assertEqual([type(f) for f in saida if isinstance(f, TTSTextFrame)], [TTSTextFrame] * 2)  # a transcrição segue

    async def test_resto_sem_pontuacao_vira_a_ultima_frase(self):
        p = wm.WhisperVoice(tts_falso({"Sim": marcado(7)}))
        saida = await ligar(p)
        for f in (texto("Sim"), TTSStoppedFrame()):
            await p.process_frame(f, D)
        await asyncio.sleep(0.2)
        self.assertEqual(valores(saida), [7])

    async def test_falha_no_meio_cai_na_reserva(self):
        p = wm.WhisperVoice(tts_falso({"Aaaaaaa.": marcado(11)}, falha_em=["Bbbbbbb."]), first_audio_timeout_s=1.0)
        saida = await ligar(p)
        await p.process_frame(audio(48000, 1000), D)
        for f in (texto("Aaaaaaa. "), texto("Bbbbbbb. "), TTSStoppedFrame()):
            await p.process_frame(f, D)
        await asyncio.sleep(0.3)
        pcm = [f for f in saida if isinstance(f, OutputAudioRawFrame) and np.frombuffer(f.audio, dtype=np.int16).any()]
        da_frase = [f for f in pcm if np.frombuffer(f.audio, dtype=np.int16)[0] == 11]
        reserva = b"".join(f.audio for f in pcm if np.frombuffer(f.audio, dtype=np.int16)[0] != 11)
        self.assertEqual(len(b"".join(f.audio for f in da_frase)), 960)  # a 1ª frase, uma vez só
        self.assertLessEqual(abs(len(reserva) - 24000), 2)
        self.assertLessEqual(abs(int(np.frombuffer(reserva, dtype=np.int16)[0]) - 251), 1)  # -12 dB de 1000
        self.assertIsInstance(saida[-1], TTSStoppedFrame)
        # o resto do turno, se o Live ainda mandar áudio, também sai baixo (turno anterior já fechado: novo turno normal)

    async def test_audio_do_live_depois_da_falha_sai_baixo(self):
        p = wm.WhisperVoice(tts_falso({}, falha_em=["Oi."]), first_audio_timeout_s=1.0)
        saida = await ligar(p)
        await p.process_frame(texto("Oi. "), D)
        await asyncio.sleep(0.2)
        await p.process_frame(audio(960, 1000), D)
        self.assertEqual(valores(saida)[-1], 251)

    async def test_timeout_do_primeiro_audio_cai_na_reserva(self):
        p = wm.WhisperVoice(tts_falso({"Oi.": marcado(11)}, atraso=5), first_audio_timeout_s=0.1)
        saida = await ligar(p)
        await p.process_frame(audio(960, 1000), D)
        for f in (texto("Oi. "), TTSStoppedFrame()):
            await p.process_frame(f, D)
        await asyncio.sleep(0.5)
        self.assertEqual(set(valores(saida)), {251})
        self.assertIsInstance(saida[-1], TTSStoppedFrame)

    async def test_sem_transcricao_o_audio_do_live_sai_baixo(self):
        p = wm.WhisperVoice(tts_falso({}))
        saida = await ligar(p)
        await p.process_frame(audio(960, 1000), D)
        await p.process_frame(TTSStoppedFrame(), D)
        await asyncio.sleep(0.2)
        self.assertEqual(set(valores(saida)), {251})

    async def test_interrupcao_cancela_tts(self):
        p = wm.WhisperVoice(tts_falso({"Oi.": marcado(11)}, atraso=0.3))
        saida = await ligar(p)
        for f in (texto("Oi. "), TTSStoppedFrame()):
            await p.process_frame(f, D)
        await asyncio.sleep(0.05)
        await p.process_frame(InterruptionFrame(), D)
        n = len(saida)
        await asyncio.sleep(0.6)
        self.assertEqual(valores(saida), [])
        self.assertEqual(len(saida), n)
        self.assertTrue(any(isinstance(f, InterruptionFrame) for f in saida))
        self.assertTrue(any(isinstance(f, TTSStoppedFrame) for f in saida))  # o fim de turno segurado segue

    async def test_turno_seguinte_nao_se_mistura_com_o_anterior(self):
        p = wm.WhisperVoice(tts_falso({"Um.": marcado(1), "Dois.": marcado(2)}, atraso=0.05))
        saida = await ligar(p)
        for f in (texto("Um. "), TTSStoppedFrame(), texto("Dois. "), TTSStoppedFrame()):
            await p.process_frame(f, D)
        await asyncio.sleep(0.5)
        self.assertEqual(valores(saida), [1, 2])


@unittest.skipUnless(IN_CONTAINER, "needs pipecat: runs in the friday image (scripts/test-all.sh)")
class TestMarcaDeFala(unittest.IsolatedAsyncioTestCase):
    """Revisão de 20/10 (caso real 19:18, previsão do tempo): com OutputAudioRawFrame simples o transporte não vê o bot
    falando — sem BotStarted/StoppedSpeaking a Pi não recebe `ela_comecou` (a espera curta de 8 s cortou a resposta)
    e o RegistroObserver não fecha o turno (conversa fora do Logbook)."""
    async def test_audio_sai_como_tts_audio(self):
        p = wm.WhisperVoice(tts_falso({"Oi.": marcado(11)}))
        saida = await ligar(p)
        for f in (audio(960), texto("Oi. "), TTSStoppedFrame()):
            await p.process_frame(f, D)
        await asyncio.sleep(0.2)
        audios = [f for f in saida if isinstance(f, OutputAudioRawFrame)]
        self.assertTrue(audios)
        self.assertTrue(all(isinstance(f, TTSAudioRawFrame) for f in audios), [type(f).__name__ for f in audios])

    async def test_primeiro_audio_do_live_marca_a_fala_na_hora(self):
        p = wm.WhisperVoice(tts_falso({}))
        saida = await ligar(p)
        await p.process_frame(audio(960), D)
        marcas = [f for f in saida if isinstance(f, TTSAudioRawFrame)]
        self.assertEqual(len(marcas), 1)
        self.assertFalse(np.frombuffer(marcas[0].audio, dtype=np.int16).any())  # silêncio
        await p.process_frame(audio(960), D)
        self.assertEqual(len([f for f in saida if isinstance(f, TTSAudioRawFrame)]), 1)  # uma marca por turno

    async def test_reserva_sai_como_tts_audio(self):
        p = wm.WhisperVoice(tts_falso({}, falha_em={"Oi."}))
        saida = await ligar(p)
        for f in (audio(4800), texto("Oi. "), TTSStoppedFrame()):
            await p.process_frame(f, D)
        await asyncio.sleep(0.2)
        audios = [f for f in saida if isinstance(f, OutputAudioRawFrame)]
        self.assertTrue([f for f in audios if np.frombuffer(f.audio, dtype=np.int16).any()])
        self.assertTrue(all(isinstance(f, TTSAudioRawFrame) for f in audios))


@unittest.skipUnless(IN_CONTAINER, "needs pipecat: runs in the friday image (scripts/test-all.sh)")
class TestPiSpeechTurns(unittest.IsolatedAsyncioTestCase):
    async def test_pi_speech_turns_idempotente(self):
        p = wm.PiSpeechTurns(min_audio_s=0)
        saida = await ligar(p)
        await p.started(); await p.started(); await p.stopped(); await p.stopped()
        self.assertEqual([type(f) for f in saida], [UserStartedSpeakingFrame, UserStoppedSpeakingFrame])

    async def test_espera_o_live_ficar_pronto_e_mantem_a_ordem(self):
        pronto = [False]
        p = wm.PiSpeechTurns(ready=lambda: pronto[0], min_audio_s=0)
        saida = await ligar(p)
        await p.started(); await p.stopped()
        await asyncio.sleep(0.1)
        self.assertEqual(saida, [])
        pronto[0] = True
        await asyncio.sleep(0.2)
        self.assertEqual([type(f) for f in saida], [UserStartedSpeakingFrame, UserStoppedSpeakingFrame])


def mic(segundos):
    return InputAudioRawFrame(audio=bytes(int(16000 * 2 * segundos)), sample_rate=16000, num_channels=1)


@unittest.skipUnless(IN_CONTAINER, "needs pipecat: runs in the friday image (scripts/test-all.sh)")
class TestJanelaComAudio(unittest.IsolatedAsyncioTestCase):
    """Revisão de 21/10 (reproduzido na bancada): activity_start + activity_end SEM áudio entre eles derruba a sessão
    do Gemini (1007 Precondition check failed). Acontecia quando a frase sussurrada acabava antes de a conexão abrir:
    a Pi mandava "começou" e "parou" juntos e o áudio ainda estava retido. O "parou" espera áudio dentro da janela."""
    async def test_fim_espera_o_audio_passar(self):
        p = wm.PiSpeechTurns()
        saida = await ligar(p)
        await p.started(); await p.stopped()
        self.assertEqual([type(f) for f in saida], [UserStartedSpeakingFrame])
        await p.process_frame(mic(0.1), D)
        self.assertNotIn(UserStoppedSpeakingFrame, [type(f) for f in saida])
        await p.process_frame(mic(0.15), D)
        self.assertEqual([type(f) for f in saida],
                         [UserStartedSpeakingFrame, InputAudioRawFrame, InputAudioRawFrame, UserStoppedSpeakingFrame])

    async def test_com_audio_suficiente_o_fim_sai_na_hora(self):
        p = wm.PiSpeechTurns()
        saida = await ligar(p)
        await p.started()
        await p.process_frame(mic(0.3), D)
        await p.stopped()
        self.assertEqual(type(saida[-1]), UserStoppedSpeakingFrame)

    async def test_nova_fala_cancela_o_fim_pendente(self):
        p = wm.PiSpeechTurns()
        saida = await ligar(p)
        await p.started(); await p.stopped(); await p.started()
        await p.process_frame(mic(0.5), D)
        self.assertNotIn(UserStoppedSpeakingFrame, [type(f) for f in saida])

    async def test_sem_audio_nenhum_o_fim_sai_depois_do_prazo(self):
        p = wm.PiSpeechTurns(stop_timeout_s=0.2)
        saida = await ligar(p)
        await p.started(); await p.stopped()
        await asyncio.sleep(0.4)
        self.assertEqual([type(f) for f in saida], [UserStartedSpeakingFrame, UserStoppedSpeakingFrame])


if __name__ == "__main__":
    unittest.main()
