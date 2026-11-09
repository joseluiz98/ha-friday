# Tarefas avulsas antes do gateway (01/10). Scripts *.sh em $AVULSOS rodam uma vez, em ordem, com o
# gateway ainda parado: comandos do CLI que precisam do container (ex.: `openclaw memory forget`, que
# depende do sqlite-vec da imagem) sem concorrer com o estado vivo. Depois de rodar, o script vira
# *.sh.feito. A saída vai para o log do container com o prefixo [avulso].
# Segurança: roda como root no container, o mesmo poder que o exec da Sexta-feira já tem.
# Testes: bash /opt/friday/image/test_avulsos.sh
rodar_avulsos() {
  local script nome rc
  for script in "$AVULSOS"/*.sh; do
    [ -f "$script" ] || continue
    nome="$(basename "$script")"
    log "tarefa avulsa: $nome"
    bash "$script" > >(sed -u 's/^/[avulso] /') 2>&1
    rc=$?
    log "tarefa avulsa $nome terminou com código $rc"
    mv "$script" "$script.feito"
  done
}
