#!/bin/sh
# Envia a saúde da Pi ao Home Assistant (webhook local). Rodado a cada 60 s por
# satelite-status.timer. Custo: ~50 ms de CPU por envio, nada entre envios.
# O id do webhook fica em /etc/satelite/webhook_id (root, 600). Nada é gravado no cartão:
# a amostra anterior da CPU vive em /run (memória).
set -u
HA_HOST=$(cat /etc/satelite/ha_host) || exit 1   # 09/11: endereço do HA fora do código (ex.: 192.0.2.10)
HA="http://$HA_HOST:8123/api/webhook"
ID=$(cat /etc/satelite/webhook_id) || exit 1
ANT=/run/satelite-cpu.ant

# CPU: fração ocupada desde o envio anterior (primeira volta = desde o boot).
set -- $(head -1 /proc/stat)
total=$(( $2 + $3 + $4 + $5 + $6 + $7 + $8 + $9 ))
ocioso=$(( $5 + $6 ))
if [ -f "$ANT" ]; then read t0 o0 < "$ANT"; else t0=0; o0=0; fi
echo "$total $ocioso" > "$ANT"
dt=$(( total - t0 )); do_=$(( ocioso - o0 ))
cpu=0; [ "$dt" -gt 0 ] && cpu=$(( (100 * (dt - do_) + dt / 2) / dt ))

temp=$(vcgencmd measure_temp | sed 's/[^0-9.]//g')
clock=$(( $(vcgencmd measure_clock arm | cut -d= -f2) / 1000000 ))
throttled=$(( $(vcgencmd get_throttled | cut -d= -f2) ))
mem_total=$(awk '/^MemTotal/{print int($2/1024)}' /proc/meminfo)
mem_disp=$(awk '/^MemAvailable/{print int($2/1024)}' /proc/meminfo)
disco=$(df -P / | awk 'NR==2{gsub("%","",$5); print $5}')
boot=$(awk '/^btime/{print $2}' /proc/stat)
ip=$(ip -4 route get "$HA_HOST" 2>/dev/null | awk '{for(i=1;i<NF;i++) if($i=="src") print $(i+1)}')

# 06/10: programa do satélite (friday-satelite) e não perturbe, para o painel do HA.
# >>> programa
# Revisão final I3 (08/10): em loop de reinício (Restart=always, 3 s) a unidade aparece "active" durante os
# segundos de import e o painel piscava. Só conta como rodando depois de 30 s ativa sem reiniciar.
programa=false
if systemctl is-active -q friday-satelite; then
  desde=$(systemctl show -p ActiveEnterTimestampMonotonic --value friday-satelite)
  agora=$(awk '{printf "%d", $1 * 1000000}' /proc/uptime)
  [ $(( (agora - desde) / 1000000 )) -ge 30 ] && programa=true
fi
# <<< programa
np=$(grep -q '"nao_perturbe": *true' /etc/friday-satelite/config.json 2>/dev/null && echo true || echo false)
# >>> extras
# Spec 2026-10-12: IP do Wi-Fi (o add-on tenta por ele quando o cabo não responde) e timers ativos (painel).
ip_wifi=$(ip -4 -br addr show wlan0 2>/dev/null | awk '{split($3, a, "/"); print a[1]}')
timers=$(python3 -c 'import json, sys; print(len(json.load(open(sys.argv[1]))))' \
  "${TIMERS_ARQ:-/home/jose/satelite/estado/timers.json}" 2>/dev/null || echo 0)
# <<< extras

curl -s -m 10 -o /dev/null -X POST -H "Content-Type: application/json" "$HA/$ID" -d "{\"temp\":$temp,\"cpu\":$cpu,\"clock_mhz\":$clock,\"throttled\":$throttled,\"mem_total_mb\":$mem_total,\"mem_disp_mb\":$mem_disp,\"disco_pct\":$disco,\"boot\":$boot,\"ip\":\"$ip\",\"programa\":$programa,\"nao_perturbe\":$np,\"ip_wifi\":\"$ip_wifi\",\"timers_ativos\":$timers}"
