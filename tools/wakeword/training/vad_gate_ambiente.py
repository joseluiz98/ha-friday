"""Cruza os disparos simulados no áudio de ambiente (vad_gate_trial.py sim) com os eventos ao vivo da Pi (03/11/2026).
Uso: vad_gate_ambiente.py <sim.json> <eventos_palavra.ndjson> [regra ...]
Cada disparo vira um instante de relógio (ambiente_AAAAMMDD_HHMMSS.wav + t). Classes:
  ao_vivo_ativou  = a Pi ativou a <= 3 s (o dono chamou e ela abriu)          -> acerto que já existe
  ao_vivo_barrada = a Pi registrou sem_voz/barrada a <= 3 s (ouviu, barrou)  -> candidato a "Friday" recuperado OU
                                                                              música que passaria: precisa ouvir
  sem_evento      = nada ao vivo perto                                       -> disparo novo: precisa ouvir
"""
import datetime, json, re, sys

JANELA = 3.0


def instante(nome, t):
    m = re.search(r"(\d{8}_\d{6})", nome)
    return datetime.datetime.strptime(m.group(1), "%Y%m%d_%H%M%S") + datetime.timedelta(seconds=t)


def main(sim_arq, ev_arq, regras):
    sim = json.load(open(sim_arq))
    ev = []
    for l in open(ev_arq, errors="replace"):
        try:
            d = json.loads(l)
        except ValueError:
            continue
        if d.get("palavra", "friday") != "friday":
            continue
        ev.append((datetime.datetime.fromisoformat(d["ts"]), d["tipo"], d.get("nota"), d.get("voz")))
    regras = regras or list(sim)
    for r in regras:
        cls = {"ao_vivo_ativou": [], "ao_vivo_barrada": [], "sem_evento": []}
        for nome, t in sim[r]["falsos_neg_onde"]:
            quando = instante(nome, t)
            perto = [e for e in ev if abs((e[0] - quando).total_seconds()) <= JANELA]
            if any(e[1] in ("ativou", "sussurro") for e in perto):
                c = "ao_vivo_ativou"
            elif any(e[1] in ("sem_voz", "barrada_verificador") for e in perto):
                c = "ao_vivo_barrada"
            else:
                c = "sem_evento"
            cls[c].append(f"{quando:%H:%M:%S} ({nome} @{t:.1f}s)")
        h = sim[r]["horas_neg"]
        print(f"\n{r}: {sum(map(len, cls.values()))} disparos em {h:.2f} h | "
              + " | ".join(f"{k} {len(v)}" for k, v in cls.items()))
        for k in ("ao_vivo_barrada", "sem_evento"):
            for x in cls[k]:
                print(f"   {k:16} {x}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], sys.argv[3:])
