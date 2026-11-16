#!/usr/bin/env bash
# Remendo do pool de threads do OpenClaw (spec docs/superpowers/specs/2026-10-11-friday-latencia-voz-design.md §4.1).
# O pool descarta a thread parada após 60 s (padrão `idleTimeoutMs ?? 6e4`), e neste Celeron N3350 recriá-la custa
# 12–37 s na pergunta seguinte (diagnóstico de 11/10). Troca SÓ o temporizador de ociosidade por `?? 0` ("nunca
# fechar"). A inscrição no aviso openclaw.memory.critical fica intacta: sob pressão de memória o próprio OpenClaw
# ainda aposenta as threads paradas. Só o pool da raiz de dist/ (o do motor de memória).
# Assinatura exata ou nada: numa versão nova do OpenClaw, sai com código != 0 e o arquivo fica intacto.
# Formas conhecidas da linha (spec 2026-10-12 D10): a da 2026.9.6 (`this.options…`), a da 2026.9.9 (`options…`,
# pool reescrito com uma janela quente de 5 min) e a da 2026.11.3 (o pool foi para src-*.mjs e ganhou slots de
# rajada: só o temporizador normal vira `?? 0`; o da rajada fica em 60 s, senão as threads extras nunca fechariam).
# O alvo é o único chunk com hash no nome (`nome-<hash>.mjs`, hash com maiúscula ou dígito) da raiz de dist/ que contém uma forma conhecida
# (original ou remendada). Na 2026.11.3 há cópias do pool em pacotes autônomos sem hash
# (managed-handoff-runtime.mjs, package-update-activation-recovery.mjs): não são o gateway e ficam intactas.
# Exatamente uma forma, uma vez, ou nada.
# Uso: remendo_pool.sh <dist> aplicar|reverter        Testes: bash /opt/friday/image/test_remendo_pool.sh
set -uo pipefail
FORMAS=('const idleMs = this.options.idleTimeoutMs ?? 6e4;'
        'const idleMs = options.idleTimeoutMs ?? 6e4;'
        'const idleMs = (burst ? options.burstIdleTimeoutMs : options.idleTimeoutMs) ?? 6e4;')
REMENDOS=('const idleMs = this.options.idleTimeoutMs ?? 0;'
          'const idleMs = options.idleTimeoutMs ?? 0;'
          'const idleMs = burst ? options.burstIdleTimeoutMs ?? 6e4 : options.idleTimeoutMs ?? 0;')
INSCRICAO='(this.options.idleTimeoutMs ?? 6e4) > 0'

falhar() { echo "$*"; exit 1; }
contar() { grep -cF -- "$1" "$2"; }

dist="${1:-}"
acao="${2:-}"
case "$acao" in aplicar|reverter) ;; *) falhar "uso: remendo_pool.sh <dist> aplicar|reverter" ;; esac
[ -d "$dist" ] || falhar "dist não encontrado: $dist"
shopt -s nullglob
# Um grep só sobre os chunks com hash (são ~9 mil .mjs na 2026.11.3; um grep por arquivo levava ~80 s na VM).
padroes=(); for i in "${!FORMAS[@]}"; do padroes+=(-e "${FORMAS[$i]}" -e "${REMENDOS[$i]}"); done
candidatos=()
for f in "$dist"/*.mjs; do
  h="${f##*/}"; h="${h%.mjs}"; h="${h##*-}"  # hash do chunk: 6+ caracteres com maiúscula ou dígito ("runtime" não é)
  [[ "$h" =~ ^[A-Za-z0-9_]{6,}$ && "$h" =~ [A-Z0-9] ]] && candidatos+=("$f")
done
arquivos=()
[ "${#candidatos[@]}" -gt 0 ] && mapfile -t arquivos < <(printf '%s\0' "${candidatos[@]}" | xargs -0 grep -lF "${padroes[@]}" --)
[ "${#arquivos[@]}" -eq 0 ] && falhar "nenhum .mjs em $dist com a assinatura esperada (OpenClaw atualizado?); nada mudou"
[ "${#arquivos[@]}" -eq 1 ] || falhar "esperava 1 arquivo do pool (.mjs com o temporizador) em $dist, achei ${#arquivos[@]}; nada mudou"
alvo="${arquivos[0]}"
nome="$(basename "$alvo")"

# Escolhe a forma presente: soma original + remendada de cada uma; só vale se exatamente uma aparece, uma vez.
TEMPORIZADOR=""; REMENDADO=""; achadas=0
for i in "${!FORMAS[@]}"; do
  n=$(( $(contar "${FORMAS[$i]}" "$alvo") + $(contar "${REMENDOS[$i]}" "$alvo") ))
  [ "$n" -gt 0 ] && { achadas=$((achadas + n)); TEMPORIZADOR="${FORMAS[$i]}"; REMENDADO="${REMENDOS[$i]}"; }
done
[ "$achadas" -eq 1 ] || falhar "assinatura diferente da esperada em $nome (OpenClaw atualizado?); nada mudou"

remendado() { [ "$(contar "$REMENDADO" "$alvo")" = 1 ] && [ "$(contar "$TEMPORIZADOR" "$alvo")" = 0 ] && [ "$(contar "$INSCRICAO" "$alvo")" = 1 ]; }
original() { [ "$(contar "$TEMPORIZADOR" "$alvo")" = 1 ] && [ "$(contar "$REMENDADO" "$alvo")" = 0 ] && [ "$(contar "$INSCRICAO" "$alvo")" = 1 ]; }

if [ "$acao" = aplicar ]; then
  remendado && { echo "já aplicado em $nome"; exit 0; }
  original || falhar "assinatura diferente da esperada em $nome (OpenClaw atualizado?); nada mudou"
  [ -f "$alvo.orig" ] || cp -p "$alvo" "$alvo.orig" || falhar "não consegui guardar o original de $nome; nada mudou"
  tmp="$alvo.novo.$$"
  python3 -c 'import sys; a, t, r = sys.argv[1:]; s = open(a).read(); sys.stdout.write(s.replace(t, r, 1))' \
    "$alvo" "$TEMPORIZADOR" "$REMENDADO" > "$tmp" || { rm -f "$tmp"; falhar "troca falhou; nada mudou"; }
  cat "$tmp" > "$alvo"
  rm -f "$tmp"
  remendado && { echo "aplicado em $nome"; exit 0; }
  cat "$alvo.orig" > "$alvo"
  falhar "resultado inesperado em $nome; original restaurado"
fi

if [ -f "$alvo.orig" ]; then
  cat "$alvo.orig" > "$alvo"
  original && { echo "revertido em $nome"; exit 0; }
  falhar "o original guardado de $nome não confere"
fi
original && { echo "já original em $nome"; exit 0; }
falhar "$nome não está no estado original e não há .orig"
