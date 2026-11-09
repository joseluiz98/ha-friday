#!/bin/sh
# Cópia diária do essencial da F.R.I.D.A.Y. para um lugar que o backup do Drive leva (spec 2026-10-18 §9; plano Task 12).
# Entra: o workspace, o openclaw.json com os segredos trocados por "REDACTED" e todo *.sqlite de home/.openclaw (cópia
# consistente pelo `.backup`, com as tabelas de credenciais esvaziadas e VACUUM, para não sobrar nada nas páginas livres).
# Fica fora: secrets/, cache/, state/ (tokens do canal da casa; entra só state/skills/skills.json, o registro das
# habilidades ensinadas à voz), credenciais, cache/tmp/logs/media do OpenClaw.
# Os segredos só existem no backup do HDD (vzdump da VM). Guarda os FRIDAY_BACKUP_KEEP (3) mais novos.
# Sem o /share montado sai com erro sem tocar no destino (o Samba fora não pode virar "backup vazio").
set -eu
umask 077
ROOT=${FRIDAY_ROOT:-/srv/friday}
DEST=${FRIDAY_BACKUP_DIR:-/mnt/haos/share/friday/backup}
SHARE=${FRIDAY_SHARE_MOUNT:-/mnt/haos/share}
SHARE_FSTYPE=${FRIDAY_SHARE_FSTYPE:-cifs}
KEEP=${FRIDAY_BACKUP_KEEP:-3}
DATE=${FRIDAY_BACKUP_DATE:-$(date +%Y%m%d)}
OC=$ROOT/home/.openclaw

# Samba de verdade, não só a camada automática (autofs passa no `mountpoint`, revisão de 19/10): o `ls` dispara a
# montagem automática e o findmnt exige o tipo cifs.
timeout 15 ls "$SHARE" >/dev/null 2>&1 || true
findmnt -n -t "$SHARE_FSTYPE" "$SHARE" >/dev/null || { echo "essentials_backup: $SHARE ($SHARE_FSTYPE) não está montado; nada feito" >&2; exit 2; }
mkdir -p "$DEST"

W=$(mktemp -d); trap 'rm -rf "$W"' EXIT
S=$W/tree/home/.openclaw; mkdir -p "$S"

cp -a "$OC/workspace" "$S/workspace"

# Registro de habilidades ensinadas à voz (23/10): só este arquivo de state/, nunca os *.token
if [ -f "$ROOT/state/skills/skills.json" ]; then
  mkdir -p "$W/tree/state/skills"
  cp "$ROOT/state/skills/skills.json" "$W/tree/state/skills/skills.json"
fi

jq 'def secret: test("token|secret|password|apikey|api_key"; "i");
    walk(if type == "object" then with_entries(if (.key | secret) and (.value | type) == "string"
         then .value = "REDACTED" else . end) else . end)' "$OC/openclaw.json" > "$S/openclaw.json"

# colunas que guardam credencial: a tabela inteira é esvaziada na cópia (levantadas na 2026.9.9 em 19/10)
SECRET_COLS="'token','tokens_json','private_key_pem','store_json','hmac_key','credential_hash','claim_token','dispatch_token'"
SECRET_TABLES="'audit_identity_keys','device_pairing_pending','device_identities','secret_store_entries'"
# o cofre do OpenClaw (secret_store_entries) guarda o valor das SecretRef do openclaw.json, como o botToken do
# Telegram (revisão final de 19/10); toda tabela com "secret" no nome também é esvaziada
SECRET_NAME_LIKE='%secret%'
# Segredos por VALOR (19/10, banco real: a chave do Gemini no retrato da config resolvida e o token do canal da casa em
# transcrições de ferramentas). Todo valor conhecido com 16+ caracteres vira REDACTED em qualquer texto dos bancos.
# A consulta ao cofre fica fora do bloco abaixo (atribuição): um erro dela aborta, em vez de encurtar a lista.
STORE=$OC/state/openclaw.sqlite
store_values=""
if [ -f "$STORE" ] && [ "$(sqlite3 -readonly -bail "$STORE" "select count(*) from sqlite_master where name = 'secret_store_entries'")" = 1 ]; then
  store_values=$(sqlite3 -readonly -bail "$STORE" "select value from secret_store_entries where value is not null")
fi
{ printf '%s\n' "$store_values"
  jq -r '[paths(type == "string") as $p | select(($p[-1] | tostring) | test("token|secret|password|apikey|api_key"; "i"))
          | getpath($p)][]' "$OC/openclaw.json"
  for f in "$ROOT"/secrets/* "$ROOT"/state/*.token; do [ -f "$f" ] && cat "$f" && echo; done
  [ -f "$ROOT/friday.env" ] && grep -iE '^[a-z0-9_]*(token|secret|key|password)[a-z0-9_]*=' "$ROOT/friday.env" | cut -d= -f2-
} | tr -d '\r' | sed 's/^[[:space:]"]*//; s/[[:space:]"]*$//' | awk 'length >= 16' | sort -u > "$W/secrets"
cat > "$W/scrub.awk" <<'EOF'
# entrada 1: um segredo por linha; entrada 2: tabela|coluna. Saída: um UPDATE por coluna que troca todos os segredos.
BEGIN { FS = "|" }
NR == FNR { gsub(/'/, "''"); s[++n] = $0; next }
n > 0 {
  col = "\"" $2 "\""; e = col; cond = ""
  for (i = 1; i <= n; i++) {
    e = "replace(" e ", '" s[i] "', 'REDACTED')"
    cond = cond (i > 1 ? " or " : "") "instr(" col ", '" s[i] "') > 0"
  }
  printf "update \"%s\" set %s = %s where typeof(%s) = 'text' and (%s);\n", $1, col, e, col, cond
}
EOF

# Tabelas virtuais ficam fora da listagem: a vec0 (sqlite-vec) do banco do agente não existe no sqlite3 da VM e o erro
# interrompia a listagem antes do auth_profile_store (19/10). Os dados delas moram em tabelas normais, varridas aqui.
# A listagem vai para uma variável (assignment) e o laço não fica num pipe: qualquer erro do sqlite3 aborta o backup.
cd "$OC"
find . \( -path ./workspace -o -path ./cache -o -path ./tmp -o -path ./logs -o -path ./media -o -path ./credentials \) -prune \
     -o -type f -name '*.sqlite' ! -name '*lock.sqlite' ! -name '*-writer.sqlite' -print > "$W/dbs"
while read -r db; do
  mkdir -p "$S/$(dirname "$db")"
  sqlite3 -readonly "$db" ".backup '$S/$db'"
  tables=$(sqlite3 -bail "$S/$db" "select distinct m.name
             from (select name from sqlite_master where type='table' and sql not like 'CREATE VIRTUAL TABLE%') m
             join pragma_table_info(m.name) p
             where p.name in ($SECRET_COLS) or m.name in ($SECRET_TABLES) or m.name like '$SECRET_NAME_LIKE'")
  for t in $tables; do
    sqlite3 -bail "$S/$db" "pragma secure_delete=on; delete from \"$t\";" >/dev/null
  done
  # Por valor: tabelas normais (sem as internas das virtuais), depois as fts5 com texto próprio (o UPDATE pela própria
  # fts5 mantém o índice coerente) e por fim o rebuild das fts5 que apontam para outra tabela (content=).
  sqlite3 -bail "$S/$db" "select m.name || '|' || p.name
      from (select name from sqlite_master t where type = 'table' and sql not like 'CREATE VIRTUAL TABLE%'
              and not exists (select 1 from sqlite_master v where v.sql like 'CREATE VIRTUAL TABLE%'
                                and substr(t.name, 1, length(v.name) + 1) = v.name || '_')
            union all
            select name from sqlite_master where sql like 'CREATE VIRTUAL TABLE%USING fts5%' and sql not like '%content%') m
      join pragma_table_info(m.name) p" > "$W/cols"
  { echo "pragma secure_delete=on;"
    awk -f "$W/scrub.awk" "$W/secrets" "$W/cols"
    sqlite3 -bail "$S/$db" "select 'insert into \"' || name || '\"(\"' || name || '\") values(''rebuild'');' from sqlite_master
                            where sql like 'CREATE VIRTUAL TABLE%USING fts5%' and sql like '%content%'"
  } > "$W/scrub.sql"
  sqlite3 -bail "$S/$db" < "$W/scrub.sql" >/dev/null
  sqlite3 -bail "$S/$db" "pragma journal_mode=delete; vacuum;" >/dev/null
done < "$W/dbs"
cd /

out=$DEST/friday-essentials-$DATE.tar.zst
tar -C "$W/tree" -cf - . | zstd -q -T0 -19 -o "$W/out.tar.zst"
chmod 600 "$W/out.tar.zst"
cp "$W/out.tar.zst" "$DEST/.friday-essentials-$DATE.partial"
mv "$DEST/.friday-essentials-$DATE.partial" "$out"

ls "$DEST" | grep -E '^friday-essentials-[0-9]{8}\.tar\.zst$' | sort -r | tail -n +"$((KEEP + 1))" | while read -r old; do
  rm -f "$DEST/$old"
done
echo "essentials_backup: $out ($(du -h "$out" | cut -f1))"
