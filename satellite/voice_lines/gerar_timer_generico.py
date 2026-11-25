#!/usr/bin/env python3
"""Gera o aviso genérico de timer ("Seu timer terminou.") com a MESMA cadeia dos avisos com nome que o bot faz ao
criar cada timer: `voice_settings.tts()` (voz, modelo e ritmo de produção) + `anuncios.falar` + `wav_48k_estereo`,
para o genérico e o nomeado soarem iguais. Roda onde o bot roda (precisa do pipecat e da chave do Gemini): na VM,
dentro do contêiner `friday`, com PYTHONPATH=/srv/friday/app e GEMINI_API_KEY_FILE apontando para a chave.
O WAV (48 kHz estéreo) vai para satellite/program/sons/timer_generico.wav e é commitado: a imagem e o OTA o levam
junto do programa. É a rede de segurança do aviso falado de cada timer (spec 2026-11-24).
Uso: python gerar_timer_generico.py <pasta_saida> --confirm-paid   (modelo pago: com permissão do dono)"""
import asyncio, os, sys

TEXTO = "Seu timer terminou."
ARQUIVO = "timer_generico.wav"


async def _gerar(saida):
    from google import genai
    import anuncios as an
    import voice_settings as vs
    with open(os.environ["GEMINI_API_KEY_FILE"]) as f:
        cli = genai.Client(api_key=f.read().strip())
    tts = vs.tts(vs.ler())
    print("voz:", tts["voice"], "modelo:", tts["model"], "ritmo:", tts["rate"], flush=True)
    pcm, _ = await asyncio.wait_for(an.falar(cli, tts, TEXTO), 60)
    wav = an.wav_48k_estereo(pcm)
    os.makedirs(saida, exist_ok=True)
    with open(os.path.join(saida, ARQUIVO), "wb") as f:
        f.write(wav)
    print(ARQUIVO, round((len(wav) - 44) / 4 / 48000, 1), "s", flush=True)
    return 0


def main(argv):
    if len(argv) < 1 or "--confirm-paid" not in argv:  # uma chamada paga: só com a permissão do dono
        print("uso: gerar_timer_generico.py <pasta_saida> --confirm-paid (chama um modelo pago)", file=sys.stderr)
        return 2
    return asyncio.run(_gerar(argv[0]))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
