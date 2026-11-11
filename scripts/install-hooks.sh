#!/usr/bin/env bash
# Installs the hooks that run the sweep (private list when present). Run once per clone.
#   pre-commit: the staged blobs (what the commit records, not the working tree)
#   pre-push:   every commit of the refs being pushed (--pre-push: full history of each, not only the new commits),
#               so nothing already committed slips out and an unpushed local branch does not block the push
set -euo pipefail
cd "$(dirname "$0")/.."
hooks=$(git rev-parse --git-path hooks)
mkdir -p "$hooks"
printf '#!/usr/bin/env bash\nexec python3 "$(git rev-parse --show-toplevel)/scripts/sweep.py" --staged\n' > "$hooks/pre-commit"
printf '#!/usr/bin/env bash\nr="$(git rev-parse --show-toplevel)"\nexec python3 "$r/scripts/sweep.py" --pre-push "$r"\n' > "$hooks/pre-push"
chmod +x "$hooks/pre-commit" "$hooks/pre-push"
echo "hooks installed in $hooks"
