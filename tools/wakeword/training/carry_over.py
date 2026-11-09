"""Ciclos cumulativos (26/10/2026): garante que nada usado num ciclo anterior se perde no ciclo novo. Roda no HA.

Uso: carry_over.py <ciclo_novo> <ciclo_anterior>...   (pastas /share/wakeword-data/<modelo>/cycle-<data>)
1. Item de TREINO de um ciclo anterior cujo arquivo de origem não está no manifest novo (a Pi apagou o áudio, ou
   ele saiu da coleta) -> o recorte enviado naquele ciclo (upload/<positive|negative>/) é copiado para o upload novo.
2. Confere o vazamento: nenhum item que foi PROVA (e nenhum arquivo que foi todo prova) em ciclo anterior pode ser
   treino no ciclo novo (build_dataset.py --pin-from garante; aqui é a conferência). Sai 3 se vazou.
Imprime o resumo; não apaga nada."""
import json, os, re, shutil, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dataset_rules as dr


def safe(iid):  # igual ao build_dataset.safe (lá ele vem com numpy, que o HA não tem)
    return re.sub(r"[^A-Za-z0-9_.-]", "_", iid.replace("@", "_at_"))


def ler(p):
    return [json.loads(l) for l in open(p) if l.strip()]


def main(novo, anteriores):
    m_novo = ler(f"{novo}/manifest.ndjson")
    nomes_novos = {i["name"] for i in m_novo}
    treino_novo = {i["name"] for i in m_novo if i["id"] in dr.upload_ids(m_novo)}
    copiados, ja, faltou, vazou = 0, 0, [], set()
    for ant in anteriores:
        m = ler(f"{ant}/manifest.ndjson")
        # 27/10: por item (mesmo arquivo + mesma janela) e por arquivo que foi TODO prova. No ciclo 1 da Sexta a sessão
        # foi dividida por tentativa no mesmo arquivo; pelo arquivo, isso acusava vazamento onde não há.
        ids_treino_novo = {i["id"] for i in m_novo if i["id"] in dr.upload_ids(m_novo)}
        vazou |= {i["id"] for i in m if i["split"] == "holdout"} & ids_treino_novo
        so_prova = {i["name"] for i in m if i["split"] == "holdout"} - {i["name"] for i in m if i["split"] == "train"}
        vazou |= so_prova & treino_novo
        for i in m:
            if i["id"] not in dr.upload_ids(m) or i["name"] in nomes_novos:
                continue  # não foi treino, ou o arquivo de origem continua na coleta (o recorte é refeito)
            pasta = "positive" if i["label"] == "pos" else "negative"
            arq = f"{safe(i['id'])}.wav"
            origem, destino = f"{ant}/upload/{pasta}/{arq}", f"{novo}/upload/{pasta}/carry__{arq}"
            if os.path.exists(destino):
                ja += 1
            elif os.path.exists(origem):
                shutil.copy(origem, destino); copiados += 1
            else:
                faltou.append(origem)
    print(json.dumps({"copiados_de_ciclos_anteriores": copiados, "ja_estavam": ja, "recortes_ausentes": len(faltou),
                      "prova_antiga_no_treino_novo": sorted(vazou)}, ensure_ascii=False, indent=1))
    if faltou:
        print("AUSENTES:", faltou[:10], file=sys.stderr)
    return 3 if vazou else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2:]))
