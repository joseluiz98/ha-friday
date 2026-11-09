"""Monta /home/jose/vozes a partir do acervo de 15/10 (spec 2026-10-15 D14). Roda UMA vez, na Pi:
~/oww/bin/python preparar_vozes.py ~/satelite/rotulos.json"""
import json, os, shutil, sys, wave
import verificador as vf

ACERVO, GRAV, RAIZ = "/home/jose/gravacoes/acervo_20261015", "/home/jose/gravacoes", "/home/jose/vozes"


def main(rotulos_caminho):
    r = json.load(open(rotulos_caminho))
    for d in ("_negativos", "_cache", "jose/historico/tentativas", "jose/historico/ativacoes", "jose/historico/continuas"):
        os.makedirs(os.path.join(RAIZ, d), mode=0o700, exist_ok=True)
    for raiz, dirs, _ in os.walk(RAIZ):
        os.chmod(raiz, 0o700)
    for a in r["falsas"]:
        shutil.copy2(f"{ACERVO}/{a}", f"{RAIZ}/_negativos/{a}")
    for a in r["verdadeiras"]:
        shutil.copy2(f"{ACERVO}/{a}", f"{RAIZ}/jose/historico/ativacoes/{a}")
    n = 0
    for nome in sorted(os.listdir(GRAV)):
        if not (nome.startswith("tentativas_friday_") and nome.endswith(".wav")):
            continue
        shutil.copy2(f"{GRAV}/{nome}", f"{RAIZ}/jose/historico/continuas/{nome}")  # 16/10: o treino usa o contínuo
        with wave.open(f"{GRAV}/{nome}") as w:
            taxa, pcm = w.getframerate(), w.readframes(w.getnframes())
        for k, (ini, fim) in enumerate(vf.recortes_tentativas(len(pcm) / 2 / taxa), 1):
            with wave.open(f"{RAIZ}/jose/historico/tentativas/{nome[:-4]}_{k:02d}.wav", "wb") as o:
                o.setnchannels(1); o.setsampwidth(2); o.setframerate(taxa)
                o.writeframes(pcm[int(ini * taxa) * 2:int(fim * taxa) * 2])
            n += 1
    shutil.copy2(rotulos_caminho, f"{RAIZ}/rotulos.json")
    print(json.dumps({"falsas": len(r["falsas"]), "verdadeiras": len(r["verdadeiras"]), "tentativas": n}))


if __name__ == "__main__":
    main(sys.argv[1])
