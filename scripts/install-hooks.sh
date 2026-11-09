#!/usr/bin/env bash
# Installs pre-commit and pre-push hooks that run the sweep (private list when present). Run once per clone.
set -euo pipefail
cd "$(dirname "$0")/.."
hooks=$(git rev-parse --git-path hooks)
mkdir -p "$hooks"
for h in pre-commit pre-push; do
  printf '#!/usr/bin/env bash\nexec python3 "$(git rev-parse --show-toplevel)/scripts/sweep.py"\n' > "$hooks/$h"
  chmod +x "$hooks/$h"
done
echo "hooks installed in $hooks"
