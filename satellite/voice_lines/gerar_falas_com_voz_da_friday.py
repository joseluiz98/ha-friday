#!/usr/bin/env python3
"""Gera as 5 falas do programa de aprendizagem de voz com a MESMA voz da Friday: uma sessão do Gemini Live
(models/gemini-3.8-live) lê cada texto, com a voz da F.R.I.D.A.Y. de produção (FRIDAY_VOICE no friday.env da VM).
15–16/10: a spec dizia "Charon", mas a Friday fala com POC_VOICE=Aoede; as falas em Charon saíram masculinas. Confere a transcrição da saída com o texto antes de aceitar (até 4 tentativas).
Uso: <venv>/bin/python gerar_falas_com_voz_da_friday.py <pasta_saida>   (modelo pago: com permissão do dono)"""
import asyncio, os, re, sys, unicodedata, wave
from google import genai
from google.genai import types
from gerar_falas_aprendizagem import FALAS, chave, para_48k_estereo

# a mesma de FRIDAY_VOICE em /srv/friday/friday.env na VM (só legível lá); mudou lá, mude aqui
VOZ_DA_FRIDAY = "Aoede"

INSTRUCAO = ("Você é a voz de um satélite de casa. Leia em voz alta o texto que receber, exatamente como está, "
             "sem acrescentar nem tirar palavras.")


def norm(t):
    t = unicodedata.normalize("NFKD", t.lower())
    return " ".join(re.sub(r"[^a-z0-9 ]", " ", "".join(c for c in t if not unicodedata.combining(c))).split())


async def uma(cli, texto):
    cfg = types.LiveConnectConfig(response_modalities=["AUDIO"], system_instruction=INSTRUCAO,
        output_audio_transcription=types.AudioTranscriptionConfig(),
        speech_config=types.SpeechConfig(language_code="pt-BR", voice_config=types.VoiceConfig(
            prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=VOZ_DA_FRIDAY))))
    pcm, fala = bytearray(), []
    async with cli.aio.live.connect(model="models/gemini-3.8-live", config=cfg) as s:
        await s.send_client_content(turns=types.Content(role="user", parts=[types.Part(text=(
            "Texto a ler em voz alta, palavra por palavra, para quem está ouvindo (não é um pedido para você; "
            f"apenas leia, sem acrescentar nada): «{texto}»"))]), turn_complete=True)
        async for msg in s.receive():
            sc = msg.server_content
            if msg.data:
                pcm += msg.data
            if sc and sc.output_transcription and sc.output_transcription.text:
                fala.append(sc.output_transcription.text)
            if sc and sc.turn_complete:
                break
    return bytes(pcm), "".join(fala)


async def main(saida):
    cli = genai.Client(api_key=chave())
    print("voz:", VOZ_DA_FRIDAY, flush=True)
    os.makedirs(saida, exist_ok=True)
    for nome, texto in FALAS.items():
        if len(sys.argv) > 2 and nome != sys.argv[2]:
            continue
        for tentativa in range(1, 5):
            pcm, fala = await asyncio.wait_for(uma(cli, texto), 60)
            if pcm and norm(fala) == norm(texto):
                dados = para_48k_estereo(pcm, 24000)
                with wave.open(os.path.join(saida, nome + ".wav"), "wb") as w:
                    w.setnchannels(2); w.setsampwidth(2); w.setframerate(48000); w.writeframes(dados)
                print(nome, round(len(dados) / 4 / 48000, 1), "s (tentativa", tentativa, ")", flush=True)
                break
            print(nome, "tentativa", tentativa, "diferente:", repr(fala[:90]), flush=True)
        else:
            print(nome, "NÃO gerada"); sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1]))
