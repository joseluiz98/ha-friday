#!/usr/bin/env bash
# Teste do remendo do pool de threads do OpenClaw (remendo_pool.sh), num dist/ falso com os trechos da 2026.9.6.
#   bash /opt/friday/image/test_remendo_pool.sh
set -uo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
R="$DIR/remendo_pool.sh"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
falhou=0
ok() { echo "ok   - $1"; }
erro() { echo "FALHA - $1"; falhou=1; }

novo_dist() {
  mkdir -p "$1"
  cat > "$1/worker-task-pool-abc123.mjs" <<'EOF'
	createWorker(slot) {
		if ((this.options.idleTimeoutMs ?? 6e4) > 0) {
			const pressure = channel("openclaw.memory.critical");
		}
	}
	idle(slot) {
		const idleMs = this.options.idleTimeoutMs ?? 6e4;
		if (idleMs > 0) {
		}
	}
EOF
  chmod 644 "$1/worker-task-pool-abc123.mjs"
}

D="$TMP/d1"; novo_dist "$D"; F="$D/worker-task-pool-abc123.mjs"; ORIG="$(cat "$F")"

# 1. aplicar troca só o temporizador de ociosidade.
saida="$(bash "$R" "$D" aplicar)"; rc=$?
if [ $rc -eq 0 ] && grep -qF 'const idleMs = this.options.idleTimeoutMs ?? 0;' "$F" \
   && ! grep -qF 'const idleMs = this.options.idleTimeoutMs ?? 6e4;' "$F"; then ok "aplicar troca o temporizador"; else erro "aplicar não trocou o temporizador (rc=$rc: $saida)"; fi
# 2. a inscrição no aviso de memória crítica fica intacta.
grep -qF '(this.options.idleTimeoutMs ?? 6e4) > 0' "$F" && ok "inscrição no aviso de memória intacta" || erro "inscrição alterada"
# 3. o original fica guardado.
[ "$(cat "$F.orig")" = "$ORIG" ] && ok "original guardado em .orig" || erro "original não guardado"
# 4. permissão preservada.
[ "$(stat -c %a "$F")" = 644 ] && ok "permissão preservada" || erro "permissão mudou para $(stat -c %a "$F")"
# 5. aplicar de novo é idempotente.
antes="$(cat "$F")"; bash "$R" "$D" aplicar > /dev/null && [ "$(cat "$F")" = "$antes" ] && ok "aplicar é idempotente" || erro "aplicar de novo mudou o arquivo"
# 6. reverter restaura o original.
bash "$R" "$D" reverter > /dev/null && [ "$(cat "$F")" = "$ORIG" ] && ok "reverter restaura" || erro "reverter não restaurou"
# 7. reverter num arquivo original, sem .orig, não faz nada e sai com 0.
D2="$TMP/d2"; novo_dist "$D2"
bash "$R" "$D2" reverter > /dev/null && [ ! -e "$D2/worker-task-pool-abc123.mjs.orig" ] && ok "reverter sem .orig = nada a fazer" || erro "reverter sem .orig falhou"
# 8. assinatura diferente (OpenClaw atualizado): código != 0 e arquivo intacto.
D3="$TMP/d3"; novo_dist "$D3"; F3="$D3/worker-task-pool-abc123.mjs"; sed -i 's/idleTimeoutMs ?? 6e4;/idleTimeoutMs ?? 3e4;/' "$F3"; antes="$(cat "$F3")"
saida="$(bash "$R" "$D3" aplicar)"; rc=$?
[ $rc -ne 0 ] && [ "$(cat "$F3")" = "$antes" ] && [ ! -e "$F3.orig" ] && echo "$saida" | grep -q "assinatura" \
  && ok "assinatura diferente: recusa sem mexer" || erro "assinatura diferente: rc=$rc, saída '$saida'"
# 9. dois arquivos de pool na raiz: recusa.
D4="$TMP/d4"; novo_dist "$D4"; cp "$D4/worker-task-pool-abc123.mjs" "$D4/worker-task-pool-xyz.mjs"
bash "$R" "$D4" aplicar > /dev/null; rc=$?
[ $rc -ne 0 ] && grep -qF '?? 6e4;' "$D4/worker-task-pool-abc123.mjs" && ok "dois pools: recusa" || erro "dois pools: aplicou (rc=$rc)"
# 10. ação inválida e dist inexistente: código != 0.
bash "$R" "$D" apagar > /dev/null; [ $? -ne 0 ] && ok "ação inválida recusada" || erro "ação inválida aceita"
bash "$R" "$TMP/nao-existe" aplicar > /dev/null; [ $? -ne 0 ] && ok "dist inexistente recusado" || erro "dist inexistente aceito"

# ---- 2026.9.9 (spec 2026-10-12 D10): o pool foi reescrito; o temporizador virou `options.idleTimeoutMs ?? 6e4`
# (sem `this.`) e ganhou uma janela quente de 5 min. A inscrição no aviso de memória ficou igual.
novo_dist_299() {
  mkdir -p "$1"
  cat > "$1/worker-task-pool-BQlnRNqZ.mjs" <<'EOF2'
		idle(slot) {
			const idleMs = options.idleTimeoutMs ?? 6e4;
			if (idleMs <= 0) return;
		},
	createWorker(slot) {
		if ((this.options.idleTimeoutMs ?? 6e4) > 0) {
			const pressure = channel("openclaw.memory.critical");
EOF2
  chmod 644 "$1/worker-task-pool-BQlnRNqZ.mjs"
}
# 11. aplicar na 2026.9.9 troca o temporizador novo e mantém a inscrição.
D5="$TMP/d5"; novo_dist_299 "$D5"; F5="$D5/worker-task-pool-BQlnRNqZ.mjs"; ORIG5="$(cat "$F5")"
saida="$(bash "$R" "$D5" aplicar)"; rc=$?
if [ $rc -eq 0 ] && grep -qF 'const idleMs = options.idleTimeoutMs ?? 0;' "$F5" \
   && grep -qF '(this.options.idleTimeoutMs ?? 6e4) > 0' "$F5"; then ok "2026.9.9: aplicar troca o temporizador novo"; else erro "2026.9.9: aplicar falhou (rc=$rc: $saida)"; fi
# 12. 2026.9.9: idempotente e reversível.
antes="$(cat "$F5")"; bash "$R" "$D5" aplicar > /dev/null && [ "$(cat "$F5")" = "$antes" ] && ok "2026.9.9: aplicar é idempotente" || erro "2026.9.9: aplicar de novo mudou o arquivo"
bash "$R" "$D5" reverter > /dev/null && [ "$(cat "$F5")" = "$ORIG5" ] && ok "2026.9.9: reverter restaura" || erro "2026.9.9: reverter não restaurou"
# 13. as duas formas no mesmo arquivo: ambíguo, recusa sem mexer.
D6="$TMP/d6"; novo_dist_299 "$D6"; F6="$D6/worker-task-pool-BQlnRNqZ.mjs"
printf '\t\tconst idleMs = this.options.idleTimeoutMs ?? 6e4;\n' >> "$F6"; antes="$(cat "$F6")"
bash "$R" "$D6" aplicar > /dev/null; rc=$?
[ $rc -ne 0 ] && [ "$(cat "$F6")" = "$antes" ] && ok "duas formas no mesmo arquivo: recusa" || erro "duas formas: rc=$rc"
# 14. arquivo real da 2026.9.9, se informado (OC_DIST_REAL=<pasta dist do pacote>): aplica e reverte numa cópia.
if [ -n "${OC_DIST_REAL:-}" ]; then
  D7="$TMP/d7"; mkdir -p "$D7"; cp "$OC_DIST_REAL"/worker-task-pool-*.mjs "$D7/"
  F7="$(ls "$D7"/worker-task-pool-*.mjs)"; ORIG7="$(cat "$F7")"
  bash "$R" "$D7" aplicar > /dev/null && [ "$(grep -cF 'idleTimeoutMs ?? 0;' "$F7")" = 1 ] \
    && bash "$R" "$D7" reverter > /dev/null && [ "$(cat "$F7")" = "$ORIG7" ] \
    && ok "arquivo real ($(basename "$F7")): aplica e reverte" || erro "arquivo real: falhou"
fi

exit $falhou
