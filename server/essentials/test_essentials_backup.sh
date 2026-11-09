#!/bin/sh
# Cópia do essencial da F.R.I.D.A.Y. para o backup do Drive (spec 2026-10-18 §9; plano Task 12).
# Árvore falsa com segredos marcados SEGREDO_*: nenhum pode aparecer no .tar.zst, nem nos bytes dos SQLite.
# sh test_essentials_backup.sh   (precisa de sqlite3, jq, zstd; roda na VM)
AQUI=$(cd "$(dirname "$0")" && pwd); f=0
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
R=$T/root; O=$R/home/.openclaw; B=$T/backup
ok() { echo "ok $1"; }; erro() { echo "ERRO $1"; f=1; }

mkdir -p "$O/workspace/friday" "$O/state" "$O/agents/friday/agent" "$O/credentials" "$O/cache" "$O/tmp" "$O/logs" \
         "$O/media" "$R/secrets" "$R/cache" "$R/state" "$B"
echo "# fatos da casa" > "$O/workspace/friday/AGENTS.md"
cat > "$O/openclaw.json" <<'EOF'
{"gateway":{"auth":{"token":"SEGREDO_GW"}},"channels":{"telegram":{"botToken":"SEGREDO_TG"}},
 "hooks":{"token":"SEGREDO_HOOKSTOKENVALUE"},"cron":{"enabled":true,"webhookToken":"SEGREDO_CRON"},
 "models":{"providers":{"google":{"apiKey":"SEGREDO_APIKEY"}}},"agents":{"defaults":{"model":"keepme"}}}
EOF
sqlite3 "$O/state/openclaw.sqlite" "create table cron_jobs(id text); insert into cron_jobs values('job1');
  create table device_auth_tokens(device_id text, token text); insert into device_auth_tokens values('d1','SEGREDO_DEV');
  create table device_identities(identity_key text, private_key_pem text); insert into device_identities values('i','SEGREDO_PEM');
  create table mcp_oauth_stores(store_key text, store_json text); insert into mcp_oauth_stores values('k','SEGREDO_OAUTH');
  create table audit_identity_keys(key_id text, key text); insert into audit_identity_keys values('a','SEGREDO_AUDIT');
  create table config_revision_keys(id int, hmac_key text); insert into config_revision_keys values(1,'SEGREDO_HMAC');
  create table secret_store_entries(scope_kind text, name text, value text);
  insert into secret_store_entries values('global','TELEGRAM_BOT_TOKEN','SEGREDO_TELEGRAMBOTVALUE');"
sqlite3 "$O/agents/friday/agent/openclaw-agent.sqlite" "create table chunks(text text); insert into chunks values('lembranca');"
# o banco real do agente tem tabela virtual vec0 (sqlite-vec), módulo que o sqlite3 da VM não tem, ANTES do
# auth_profile_store (19/10: a listagem das tabelas parava no erro e o auth_profile_store ia junto sem apagar)
sqlite3 -cmd ".dbconfig defensive off" "$O/agents/friday/agent/openclaw-agent.sqlite" "pragma writable_schema=on;
  insert into sqlite_master values('table','vecs','vecs',0,'CREATE VIRTUAL TABLE vecs USING vec0(embedding float[4])');" >/dev/null
sqlite3 "$O/agents/friday/agent/openclaw-agent.sqlite" \
  "create table auth_profile_store(store_key text, store_json text); insert into auth_profile_store values('p','SEGREDO_PROFILE');"
sqlite3 "$O/agents/friday/agent/openclaw-agent.sqlite.reindex-lock.sqlite" "create table l(x int);"
echo SEGREDO_AUTHFILE > "$O/agents/friday/agent/auth-profiles.json"
echo SEGREDO_CREDDIR > "$O/credentials/x"
for d in cache tmp logs media; do echo "lixo" > "$O/$d/x"; done
echo SEGREDO_GEMINIKEYVALUE > "$R/secrets/gemini_api_key"; echo lixo > "$R/cache/y"
echo SEGREDO_CANALTOKENVALUE > "$R/state/canal_casa.token"
# 23/10: o registro de habilidades ensinadas à voz é a única coisa de state/ que entra; a senha e o resto ficam fora
mkdir -p "$R/state/skills"; echo '{"skills": []}' > "$R/state/skills/skills.json"
echo '{"notify_new_skill": false}' > "$R/state/skills/config.json"
printf -- '-----BEGIN KEY-----\nSEGREDO_SSHKEYLINEVALUE\n-----END KEY-----\n' > "$R/secrets/haos_ssh"
# segredos que vazaram por VALOR em colunas comuns (19/10, banco real): retrato da config resolvida e ferramentas lidas
# numa sessão; mais as tabelas de busca (fts5 com texto próprio e fts5 apontando para outra tabela)
sqlite3 "$O/state/openclaw.sqlite" "create table config_machine_state(state_key text, value_json text);
  insert into config_machine_state values('m','{\"env\":\"SEGREDO_GEMINIKEYVALUE\",\"h\":\"SEGREDO_HOOKSTOKENVALUE\"}');"
sqlite3 "$O/agents/friday/agent/openclaw-agent.sqlite" "create table transcript_events(id int, event_json text);
  insert into transcript_events values(1,'{\"out\":\"SEGREDO_CANALTOKENVALUE e SEGREDO_SSHKEYLINEVALUE\"}');
  insert into transcript_events values(2,'{\"out\":\"bot SEGREDO_TELEGRAMBOTVALUE\"}');
  create virtual table session_transcript_fts using fts5(text, session_id unindexed);
  insert into session_transcript_fts values('a chave SEGREDO_GEMINIKEYVALUE foi lida','s1');
  create table standing_intents(intent_key integer primary key, trigger_keywords text);
  insert into standing_intents values(1,'aviso SEGREDO_CANALTOKENVALUE');
  create virtual table standing_intents_fts using fts5(trigger_keywords, content='standing_intents', content_rowid='intent_key');
  insert into standing_intents_fts(standing_intents_fts) values('rebuild');"
for d in 20261001 20261002 20261003; do echo velho > "$B/friday-essentials-$d.tar.zst"; done

# roda <ponto> [tipo]: o tipo padrão é o do / (no teste o "/" faz o papel do Samba montado)
RAIZ_FS=$(findmnt -n -o FSTYPE /)
roda() { FRIDAY_ROOT=$R FRIDAY_BACKUP_DIR=$B FRIDAY_BACKUP_DATE=20261019 FRIDAY_SHARE_MOUNT=$1 FRIDAY_SHARE_FSTYPE=${2:-$RAIZ_FS} \
           sh "$AQUI/essentials_backup.sh" >"$T/saida" 2>&1; }

# revisão final de 19/10: `mountpoint -q /share` passava só com a camada automática (autofs) e o Samba fora; a guarda
# exige o tipo de verdade (cifs). Aqui: um ponto montado, mas de outro tipo, também é "Samba fora".
roda / cifs-de-mentira; rc=$?
[ $rc -ne 0 ] && [ "$(ls "$B" | wc -l)" -eq 3 ] && ok "montagem sem o Samba de verdade: sai com $rc sem mexer" \
  || erro "montagem de outro tipo aceita como Samba (rc=$rc)"

# /share fora do ar: sai com erro e não mexe em nada
roda "$T/nao-montado"; rc=$?
[ $rc -ne 0 ] && ok "sem /share montado sai com $rc" || erro "sem /share montado saiu com 0"
[ "$(ls "$B" | wc -l)" -eq 3 ] && [ ! -e "$B/friday-essentials-20261019.tar.zst" ] && ok "sem /share nada foi criado nem apagado" \
  || erro "sem /share mexeu no destino: $(ls "$B")"

roda /; rc=$?
[ $rc -eq 0 ] && ok "execução normal sai com 0" || { erro "execução normal saiu com $rc"; cat "$T/saida"; }
A=$B/friday-essentials-20261019.tar.zst
[ -s "$A" ] && ok "gerou $(basename "$A")" || erro "não gerou $A"
[ "$(ls "$B")" = "$(printf 'friday-essentials-20261002.tar.zst\nfriday-essentials-20261003.tar.zst\nfriday-essentials-20261019.tar.zst')" ] \
  && ok "guarda só os 3 mais novos" || erro "retenção: $(ls "$B" | tr '\n' ' ')"
[ -z "$(ls -a "$B" | grep -v -E '^\.\.?$|^friday-essentials-[0-9]{8}\.tar\.zst$')" ] && ok "sem sobras no destino" || erro "sobras: $(ls -a "$B")"

X=$T/x; mkdir "$X"; zstd -dc "$A" | tar -xf - -C "$X" || erro "tar.zst ilegível"
XO=$X/home/.openclaw
[ -f "$XO/workspace/friday/AGENTS.md" ] && ok "workspace incluído" || erro "workspace ausente"
[ "$(jq -r .agents.defaults.model "$XO/openclaw.json")" = keepme ] && ok "openclaw.json incluído" || erro "openclaw.json ausente ou estragado"
for p in .gateway.auth.token .channels.telegram.botToken .hooks.token .cron.webhookToken .models.providers.google.apiKey; do
  [ "$(jq -r "$p" "$XO/openclaw.json")" = REDACTED ] && ok "$p = REDACTED" || erro "$p não foi apagado"
done
[ "$(sqlite3 "$XO/state/openclaw.sqlite" 'select id from cron_jobs')" = job1 ] && ok "banco geral incluído (cron_jobs)" || erro "banco geral ausente"
[ "$(sqlite3 "$XO/agents/friday/agent/openclaw-agent.sqlite" 'select text from chunks')" = lembranca ] && ok "banco do agente incluído" \
  || erro "banco do agente ausente"
# cofre do OpenClaw (revisão final de 19/10): o botToken do Telegram é uma SecretRef para esta tabela
[ "$(sqlite3 "$XO/state/openclaw.sqlite" 'select count(*) from secret_store_entries')" = 0 ] && ok "cofre de segredos esvaziado" \
  || erro "secret_store_entries não foi esvaziada"
[ "$(sqlite3 "$XO/state/openclaw.sqlite" 'select count(*) from device_auth_tokens')" = 0 ] && ok "tabelas de segredo esvaziadas" \
  || erro "device_auth_tokens não foi esvaziada"
[ -f "$X/state/skills/skills.json" ] && ok "registro de habilidades no backup" || erro "state/skills/skills.json ausente do backup"
[ "$(find "$X/state" -type f | wc -l)" -eq 1 ] && ok "de state/ só o registro de habilidades" || erro "state/ com mais que o registro: $(find "$X/state" -type f)"
for p in secrets cache state/canal_casa.token home/.openclaw/credentials home/.openclaw/cache home/.openclaw/tmp home/.openclaw/logs \
         home/.openclaw/media home/.openclaw/agents/friday/agent/auth-profiles.json; do
  [ ! -e "$X/$p" ] && ok "fora: $p" || erro "incluído por engano: $p"
done
XA=$XO/agents/friday/agent/openclaw-agent.sqlite
[ "$(sqlite3 "$XA" "select count(*) from transcript_events where event_json like '%REDACTED%'")" = 2 ] \
  && ok "transcrição mantida com o segredo trocado" || erro "transcript_events sem REDACTED"
[ "$(sqlite3 "$XO/state/openclaw.sqlite" "select count(*) from config_machine_state where value_json like '%REDACTED%'")" = 1 ] \
  && ok "retrato da config mantido com o segredo trocado" || erro "config_machine_state sem REDACTED"
[ "$(sqlite3 "$XA" "select count(*) from session_transcript_fts where session_transcript_fts match 'redacted AND lida'")" = 1 ] \
  && ok "busca textual acha o texto trocado" || erro "session_transcript_fts não acha o texto trocado"
for ft in session_transcript_fts standing_intents_fts; do
  sqlite3 -bail "$XA" "insert into $ft($ft) values('integrity-check')" 2>/dev/null && ok "$ft íntegro" || erro "$ft inconsistente"
done
achados=$(grep -r -a -o 'SEGREDO_[A-Z]*' "$X" | sort -u | tr '\n' ' ')
[ -z "$achados" ] && ok "nenhum SEGREDO_* nos bytes do backup" || erro "segredos no backup: $achados"
[ "$(stat -c %a "$A")" = 600 ] && ok "arquivo 600" || erro "permissão $(stat -c %a "$A")"
exit $f
