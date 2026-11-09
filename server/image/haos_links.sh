#!/bin/bash
# Atalhos do Samba do HAOS no /share LOCAL do container (desacoplamento de 19/10).
# Uso: haos_links.sh <pasta_de_atalhos> <share_do_haos> <nome_a_pular>
# Cria <pasta_de_atalhos>/<x> -> <share_do_haos>/<x> para cada entrada do Samba que ainda não tenha atalho; pula o
# apelido do HOME e nunca troca o que já existe. Com o Samba fora ou travado, desiste em 5 s e sai com 0: a partida
# da F.R.I.D.A.Y. não espera o HAOS (os atalhos fixos da imagem cobrem o essencial).
links="$1"; haos="$2"; skip="$3"
mkdir -p "$links" || exit 0
entries="$(timeout 5 ls -A "$haos" 2>/dev/null)" || exit 0
printf '%s\n' "$entries" | while IFS= read -r e; do
  [ -n "$e" ] && [ "$e" != "$skip" ] || continue
  [ -e "$links/$e" ] || [ -L "$links/$e" ] && continue
  ln -s "$haos/$e" "$links/$e"
done
exit 0
