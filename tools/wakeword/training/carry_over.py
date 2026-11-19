"""Ciclos cumulativos (26/10/2026): garante que nada usado num ciclo anterior se perde no ciclo novo. Roda no HA.

Uso: carry_over.py [--office <sat_id>] <ciclo_novo> <ciclo_anterior>...
     (pastas /srv/nobackup/wakeword-data/<modelo>/cycle-<data> na VM)
1. Item de TREINO de um ciclo anterior cujo arquivo de origem não está no manifest novo (a Pi apagou o áudio, ou
   ele saiu da coleta) -> o recorte enviado naquele ciclo (upload/<positive|negative>/) é copiado para o upload novo.
2. Confere o vazamento: nenhum item que foi PROVA (e nenhum arquivo que foi todo prova) em ciclo anterior pode ser
   treino no ciclo novo (build_dataset.py --pin-from garante; aqui é a conferência). Sai 3 se vazou.
Identidade de clipe entre ciclos por dataset_rules.clip_key (18/11/2026): `<sat_id>__X` e o nome legado `X` (do satélite
do escritório: --office, senão FRIDAY_SAT_OFFICE do ambiente ou de $FRIDAY_LOCAL_CONFIG) são o mesmo clipe. Sem o id
do escritório e com nome legado, sai 2 (nunca adivinha).
Imprime o resumo; não apaga nada."""
import json, os, re, shutil, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dataset_rules as dr


def safe(iid):  # igual ao build_dataset.safe (lá ele vem com numpy, que o HA não tem)
    return re.sub(r"[^A-Za-z0-9_.-]", "_", iid.replace("@", "_at_"))


def ler(p):
    return [json.loads(l) for l in open(p) if l.strip()]


def main(novo, anteriores, office):
    try:
        return _main(novo, anteriores, office)
    except ValueError as e:
        print(f"ERRO: {e}", file=sys.stderr)
        return 2


def _main(novo, anteriores, office):
    key = lambda n: dr.clip_key(n, office)  # noqa: E731
    m_novo = ler(f"{novo}/manifest.ndjson")
    nomes_novos = {key(i["name"]) for i in m_novo}
    treino_novo = {key(i["name"]) for i in m_novo if i["id"] in dr.upload_ids(m_novo)}
    ids_treino_novo = {key(i["id"]) for i in m_novo if i["id"] in dr.upload_ids(m_novo)}
    copiados, ja, faltou, vazou = 0, 0, [], set()
    for ant in anteriores:
        m = ler(f"{ant}/manifest.ndjson")
        # 27/10: por item (mesmo arquivo + mesma janela) e por arquivo que foi TODO prova. No ciclo 1 da Sexta a sessão
        # foi dividida por tentativa no mesmo arquivo; pelo arquivo, isso acusava vazamento onde não há.
        vazou |= {key(i["id"]) for i in m if i["split"] == "holdout"} & ids_treino_novo
        so_prova = {key(i["name"]) for i in m if i["split"] == "holdout"} - {key(i["name"]) for i in m if i["split"] == "train"}
        vazou |= so_prova & treino_novo
        for i in m:
            if i["id"] not in dr.upload_ids(m) or key(i["name"]) in nomes_novos:
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
    args = sys.argv[1:]
    off = None
    if args[:1] == ["--office"]:
        off, args = args[1], args[2:]
    if len(args) < 2:
        print(__doc__, file=sys.stderr)
        sys.exit(2)
    sys.exit(main(args[0], args[1:], off or dr.office_id()))
