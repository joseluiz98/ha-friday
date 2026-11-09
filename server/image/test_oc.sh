#!/bin/sh
# O OpenClaw guarda no SQLite chaves calculadas a partir do HOME (ex.: cron_jobs.store_key =
# <HOME>/.openclaw/cron/jobs.json). Para enxergar o estado do POC, o container roda com o HOME do apelido
# /share/openclaw-poc/home (o mesmo diretório que /srv/friday/home), definido só no compose (FRIDAY_OC_HOME);
# run.sh e oc o leem do ambiente do container.
# sh test_oc.sh
AQUI=$(dirname "$0"); E=/share/openclaw-poc/home; f=0
D=$(mktemp -d); trap 'rm -rf "$D"' EXIT
printf '#!/bin/sh\necho "$@" > %s/args\n' "$D" > "$D/docker"; chmod +x "$D/docker"
PATH="$D:$PATH" sh "$AQUI/oc" cron list
grep -qF -- 'exec -i friday sh -c HOME="${FRIDAY_OC_HOME:-$HOME}" exec openclaw "$@" oc cron list' "$D/args" && echo "ok oc usa o FRIDAY_OC_HOME do container" || { echo "ERRO oc: $(cat "$D/args")"; f=1; }
grep -q "FRIDAY_OC_HOME: $E\$" "$AQUI/docker-compose.yml" && echo "ok compose fixa FRIDAY_OC_HOME" || { echo "ERRO compose sem FRIDAY_OC_HOME: $E"; f=1; }
grep -q '^export HOME="${FRIDAY_OC_HOME:-$BASE/home}"$' "$AQUI/run.sh" && echo "ok run.sh usa FRIDAY_OC_HOME" || { echo "ERRO run.sh não usa FRIDAY_OC_HOME"; f=1; }
exit $f
