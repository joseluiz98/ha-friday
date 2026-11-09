#!/usr/bin/env bash
# Deploys ONE component from a version of the repo (run on HAOS). Exports the ref with `git archive`, ships it to the
# host and runs that component's install script with FRIDAY_VERSION_LINE="<describe> <sha>". Never edits in place.
#   scripts/deploy.sh <component> <git-ref> [user@host]
#   components: server-agent | server | voice-gateway | brain-skills | wwt  (host: the VM, /config/.friday/vm)
#               satellite <ref> jose@<pi>                              (program + agent on that Pi)
#               integration                                            (HAOS: scripts/install-integration.sh)
set -euo pipefail
cd "$(dirname "$0")/.."
COMP=${1:?component}; REF=${2:?git ref}
SHA=$(git rev-parse --verify "$REF^{commit}")
LINE="$(git describe --tags --always "$SHA") $SHA"
if [ "$COMP" = integration ]; then exec bash scripts/install-integration.sh "$SHA"; fi
HOST=${3:-$(cat /config/.friday/vm 2>/dev/null)}
[ -n "$HOST" ] || { echo "no host (pass user@host or write /config/.friday/vm)" >&2; exit 2; }
case "$COMP" in
  server-agent)  PATHS="agents scripts/vault.py vault"; RUN="cd agents && sudo FRIDAY_VERSION_LINE='$LINE' bash server/install.sh" ;;
  server)        PATHS="server voice_gateway/local_config.py"; RUN="sudo FRIDAY_VERSION_LINE='$LINE' bash server/install.sh" ;;
  voice-gateway) PATHS="voice_gateway scripts/lib"; RUN="sudo FRIDAY_VERSION_LINE='$LINE' bash voice_gateway/install.sh" ;;
  brain-skills)  PATHS="brain"; RUN="sudo FRIDAY_VERSION_LINE='$LINE' bash brain/skills/install.sh" ;;
  wwt)           PATHS="tools/wakeword/training tools/wakeword/wwt"; RUN="sudo FRIDAY_VERSION_LINE='$LINE' bash tools/wakeword/wwt/install.sh" ;;
  satellite)     PATHS="satellite/program scripts/lib agents"
                 RUN="FRIDAY_VERSION_LINE='$LINE' bash satellite/program/install.sh && cd agents && sudo FRIDAY_VERSION_LINE='$LINE' bash satellite/install.sh" ;;
  *) echo "unknown component: $COMP" >&2; exit 2 ;;
esac
DIR="friday-deploy-$COMP-${SHA:0:8}"
# shellcheck disable=SC2086
git archive --format=tar --prefix="$DIR/" "$SHA" $PATHS | ssh -o BatchMode=yes "$HOST" "rm -rf ~/$DIR && tar -x -C ~ && cd ~/$DIR && $RUN; rc=\$?; rm -rf ~/$DIR; exit \$rc"
echo "deployed $COMP $LINE to $HOST"
