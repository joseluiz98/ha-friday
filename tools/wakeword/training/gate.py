"""Portão de instalação do candidato (spec/plano 2026-10-24, Task 6). Valores FIXOS do plano.
Uso: python3 gate.py <saida_evaluate.json> <metrics.json> [--max sombra]   -> imprime {"verdict","reasons"}"""
import json, sys

ORDEM = ("reprovar", "sombra", "instalar")


def gate(r: dict, acav_fp_cand: float, max_verdict: str = "instalar") -> dict:
    atual, cand = r["atual"], r["candidato"]
    motivos = []
    if cand["recall"] <= atual["recall"]:
        motivos.append("recall não melhorou")
    if len(r["regressoes"]) > 0.05 * atual["pos_detectados"]:
        motivos.append("regressões acima de 5%")
    if cand["fp_por_hora"] > atual["fp_por_hora"]:
        motivos.append("mais disparos falsos")
    if motivos:
        veredito = "reprovar"
    else:
        if cand["recall"] - atual["recall"] < 0.10:
            motivos.append("ganho pequeno")
        if acav_fp_cand > 0.5:
            motivos.append("FP/h sintético alto")
        veredito = "sombra" if motivos else "instalar"
    if ORDEM.index(veredito) > ORDEM.index(max_verdict):
        motivos.append(f"limitado a {max_verdict}")
        veredito = max_verdict
    return {"verdict": veredito, "reasons": motivos}


def main(argv):
    mx = "instalar"
    if "--max" in argv:
        i = argv.index("--max"); mx = argv[i + 1]; argv = argv[:i] + argv[i + 2:]
    if len(argv) != 3 or mx not in ORDEM:
        print(__doc__, file=sys.stderr)
        return 2
    r, m = json.load(open(argv[1])), json.load(open(argv[2]))
    print(json.dumps(gate(r, float(m["val_fp_per_hour"]), mx), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
