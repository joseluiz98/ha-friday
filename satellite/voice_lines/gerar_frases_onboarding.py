#!/usr/bin/env python3
"""Frases pré-gravadas do primeiro boot do satélite (spec 2026-11-05 §5 #5; plano 2026-11-05-satellite-provisioning Task 15),
na MESMA voz da F.R.I.D.A.Y. (reaproveita gerar_falas_com_voz_da_friday.uma). Modelo pago: só com permissão do dono
(dada em 05/11 para esta geração).
Uso: <venv com google-genai>/bin/python gerar_frases_onboarding.py <pasta_saida> [chave]"""
import asyncio, os, sys, wave
from google import genai
from gerar_falas_aprendizagem import chave, para_48k_estereo
from google.genai import types
from gerar_falas_com_voz_da_friday import VOZ_DA_FRIDAY, norm

# 2ª versão (05/11): a 1ª pedia "palavra por palavra" e saiu lenta e mecânica. Agora: fala natural, como a voz das conversas.
INSTRUCAO = ("Você é a Sexta-feira, a assistente de voz da casa, falando por um aparelho novo. Fale em português do Brasil, "
             "de forma natural, simpática e breve, como numa conversa, em ritmo um pouco mais rápido e dinâmico. "
             "Diga exatamente o texto que receber, sem acrescentar nem tirar palavras.")


async def uma(cli, texto):
    cfg = types.LiveConnectConfig(response_modalities=["AUDIO"], system_instruction=INSTRUCAO,
        output_audio_transcription=types.AudioTranscriptionConfig(),
        speech_config=types.SpeechConfig(language_code="pt-BR", voice_config=types.VoiceConfig(
            prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=VOZ_DA_FRIDAY))))
    pcm, fala = bytearray(), []
    async with cli.aio.live.connect(model="models/gemini-3.8-live", config=cfg) as s:
        await s.send_client_content(turns=types.Content(role="user", parts=[types.Part(text=(
            f"Diga, com naturalidade, só isto: «{texto}»"))]), turn_complete=True)
        async for msg in s.receive():
            sc = msg.server_content
            if msg.data:
                pcm += msg.data
            if sc and sc.output_transcription and sc.output_transcription.text:
                fala.append(sc.output_transcription.text)
            if sc and sc.turn_complete:
                break
    return bytes(pcm), "".join(fala)

DIGITOS = ["zero", "um", "dois", "três", "quatro", "cinco", "seis", "sete", "oito", "nove"]

FRASES = {  # textos fechados com o dono em 05/11 (2ª versão)
    "setup_mode": "Oi, meu nome é Sexta-Feira! Configure esse Mark pelo aplicativo do Home Assistant.",
    "pairing_code": "Para me adicionar, digite no aplicativo Home Assistant este código",
    **{f"digit_{i}": d for i, d in enumerate(DIGITOS)},
    "updating": "Estou instalando uma atualização. Isso pode levar alguns minutos, não me desligue.",
    "update_slow": "A atualização está demorando mais do que o esperado, ainda estou trabalhando nisso!",
    "no_wifi": "Não consegui me conectar à rede Wi-Fi. Vou continuar tentando.",
    "no_server": "Não estou encontrando o servidor da Friday.",
    "say_wake_word": "Vamos testar o microfone. Diga Friday.",
    "heard_it": "Ouvi você. Agora diga Friday mais uma vez.",
    "mic_muted": "Não consegui te ouvir, o microfone está no mudo?",
    "reset_network": "Vou voltar para a rede Wi-Fi original.",
    "reset_home": "Vou voltar às configurações de fábrica.",
    "ready": "Cheguei! Tudo certo por aqui. Quando precisar, é só dizer Friday.",
    "step_failed": "Houve um problema em um passo da instalação.",
    "reset_confirm_ha": "Para confirmar a restauração às configurações de fábrica, aperte novamente o mesmo botão no Home Assistant em até dez segundos.",
    "reset_confirm_button": "Continue segurando o botão para voltar às configurações de fábrica. Solte para cancelar.",
}


def igual(a, b):
    """Transcrição × texto, tolerante a espaço e a dígito escrito como número (ex.: "3" × "três")."""
    def f(t):
        t = norm(t)
        for i, d in enumerate(DIGITOS):
            t = t.replace(str(i), norm(d))
        return t.replace(" ", "")
    return f(a) == f(b)


async def main(saida, so=None):
    cli = genai.Client(api_key=chave())
    os.makedirs(saida, exist_ok=True)
    for nome, texto in FRASES.items():
        if so and nome != so:
            continue
        for tentativa in range(1, 5):
            pcm, fala = await asyncio.wait_for(uma(cli, texto), 60)
            if pcm and igual(fala, texto):
                dados = para_48k_estereo(pcm, 24000)
                with wave.open(os.path.join(saida, nome + ".wav"), "wb") as w:
                    w.setnchannels(2); w.setsampwidth(2); w.setframerate(48000); w.writeframes(dados)
                print(nome, round(len(dados) / 4 / 48000, 1), "s (tentativa", tentativa, ")", flush=True)
                break
            print(nome, "tentativa", tentativa, "diferente:", repr(fala[:90]), flush=True)
        else:
            print(nome, "NÃO gerada", flush=True)


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None))
