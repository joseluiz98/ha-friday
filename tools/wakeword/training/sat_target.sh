# Sourced by harvest.sh and analysis/run.sh (18/11/2026): which satellite, and the address of its Pi.
#   sat_target <--sat value or ""> <--host value or "">
# Sets SAT (the clip prefix), OFFICE (owner of legacy names), PI_HOST and VM. Returns 2 with a message on error.
# - OFFICE: FRIDAY_SAT_OFFICE from the env, else $FRIDAY_LOCAL_CONFIG (default /srv/friday/state/local.env), else the
#   VM's /srv/friday/state/local.env over ssh (on the HA box the file lives only on the VM).
# - SAT: --sat, else OFFICE.
# - PI_HOST: --host; else, with --sat, the "ip" of that satellite in $FRIDAY_SATELLITES_JSON
#   (default /share/friday/satellites.json), falling back to $FRIDAY_DOT_DIR/mk1 only for the office satellite;
#   without --sat, $FRIDAY_DOT_DIR/mk1 (default /config/.friday), as before.
sat_target() {
  local sat_arg=$1 host_arg=$2 dot=${FRIDAY_DOT_DIR:-/config/.friday}
  local sats=${FRIDAY_SATELLITES_JSON:-/share/friday/satellites.json}
  VM=$(cat "$dot/vm") || { echo "missing $dot/vm" >&2; return 2; }
  OFFICE=${FRIDAY_SAT_OFFICE:-}
  [ -n "$OFFICE" ] || OFFICE=$(sed -n 's/^FRIDAY_SAT_OFFICE=//p' "${FRIDAY_LOCAL_CONFIG:-/srv/friday/state/local.env}" \
    2>/dev/null | tail -1 | tr -d "\"' ")
  [ -n "$OFFICE" ] || OFFICE=$(ssh "$VM" "sudo sed -n 's/^FRIDAY_SAT_OFFICE=//p' /srv/friday/state/local.env" \
    | tail -1 | tr -d "\"' ")
  [[ "$OFFICE" =~ ^sat-[0-9a-f]{6}$ ]] || { echo "FRIDAY_SAT_OFFICE not found or invalid (env, local.env or the VM)" >&2; return 2; }
  SAT=${sat_arg:-$OFFICE}
  [[ "$SAT" =~ ^sat-[0-9a-f]{6}$ ]] || { echo "invalid --sat: $SAT" >&2; return 2; }
  if [ -n "$host_arg" ]; then
    PI_HOST=$host_arg
  elif [ -n "$sat_arg" ]; then
    PI_HOST=$(python3 -I -c 'import json,sys
try:
    print(json.load(open(sys.argv[1]))[sys.argv[2]]["ip"])
except (OSError, ValueError, KeyError, TypeError):
    pass' "$sats" "$SAT")
    if [ -z "$PI_HOST" ] && [ "$SAT" = "$OFFICE" ]; then PI_HOST=$(cat "$dot/mk1" 2>/dev/null); fi
    [ -n "$PI_HOST" ] || { echo "no ip for $SAT in $sats (pass --host)" >&2; return 2; }
  else
    PI_HOST=$(cat "$dot/mk1") || { echo "missing $dot/mk1" >&2; return 2; }
  fi
  [[ "$PI_HOST" =~ ^[A-Za-z0-9.:-]+$ ]] || { echo "invalid Pi address: $PI_HOST" >&2; return 2; }
  return 0
}
