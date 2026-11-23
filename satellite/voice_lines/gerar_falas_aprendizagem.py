"""Texts and helpers of the recorded phrases of the voice-learning program (spec 2026-10-15 D16; 23/11 extra words).
The audio is made by gerar_falas_com_voz_da_friday.py (voice Aoede, transcript checked). This module used to live
outside the repo (tools/satelite/gerar_falas_aprendizagem.py, which also had a one-off Charon TTS main, now gone)."""
import array, os, re

# Falas para pessoas (não são textos para um modelo): instruções diretas são o certo aqui, e a voz é a da Friday em
# primeira pessoa. Nada de satélite, cérebro ou modelo (skill designing-for-friday, item 14).
FALAS = {
    "aprendizagem_intro": "Vamos começar o aprendizado da sua voz. Depois de cada bipe, diga Friday uma vez, do jeito "
                          "que você costuma me chamar. São dez vezes.",
    # 23/11: uma por palavra extra (aprendizagem_intro_<nome>); a que entra depois da primeira rodada
    "aprendizagem_intro_sexta_feira": "Agora a próxima palavra. Depois de cada bipe, diga Sexta-feira uma vez, do "
                                      "jeito que você costuma me chamar. Também são dez vezes.",
    "aprendizagem_mudar_lugar": "Muito bem. Agora vá para outro lugar do cômodo, a uns três metros daqui, e continue "
                                "depois de cada bipe.",
    # 23/11: vale para uma palavra ou para várias (antes dizia "sem dizer Friday")
    "aprendizagem_fala_livre": "Ótimo. Agora fale qualquer coisa por trinta segundos, sem repetir as palavras que você "
                               "acabou de dizer. Pode contar como foi o seu dia.",
    "aprendizagem_fim": "Pronto. Agora eu vou aprender a sua voz. Em alguns minutos eu aviso o resultado.",
    "aprendizagem_cancelada": "Aprendizagem de voz cancelada.",
}


def chave():
    """Chave do Gemini: arquivo apontado por GEMINI_API_KEY_FILE (na VM: /srv/friday/secrets/gemini_api_key) ou, no
    HAOS, `poc_gemini_api_key` do secrets.yaml. Nunca é impressa."""
    arq = os.environ.get("GEMINI_API_KEY_FILE")
    if arq:
        with open(arq) as f:
            return f.read().strip()
    for linha in open("/config/secrets.yaml"):
        m = re.match(r"poc_gemini_api_key:\s*(.*)", linha.strip())
        if m:
            return m.group(1).strip().strip("\"'")
    raise SystemExit("chave do Gemini não encontrada (GEMINI_API_KEY_FILE ou poc_gemini_api_key)")


def para_48k_estereo(pcm, taxa):
    """PCM S16 mono -> 48 kHz estéreo S16, pico normalizado em ~-6 dBFS (o formato dos sons do satélite)."""
    a = array.array("h"); a.frombytes(pcm)
    pico = max(1, max(abs(v) for v in a))
    out = array.array("h")
    for i in range(int(len(a) * 48000 / taxa)):
        pos = i * taxa / 48000
        j, f = int(pos), pos - int(pos)
        v = a[j] if j + 1 >= len(a) else a[j] * (1 - f) + a[j + 1] * f
        s = int(v / pico * 16000)
        out.append(s); out.append(s)
    return out.tobytes()
