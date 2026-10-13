#!/bin/bash
# Put the latest Drishti branch live on this Pi, and prove it is live.
#
#     bash scripts/deploy_garuda.sh            # the Drishti branch
#     bash scripts/deploy_garuda.sh SomeBranch # another branch
#     bash scripts/deploy_garuda.sh --check    # change nothing: is every fix live?
#
# Pushing to GitHub changes nothing here: the service runs whatever this
# checkout holds. This fetches the branch, moves the checkout to it (fast-
# forward only, never a merge), restarts garuda-web and then asks the running
# service which commit it started from (/api/health), so "is it deployed?" has
# an answer instead of a guess. It stops, changing nothing, if this checkout
# has local edits.
set -euo pipefail

BRANCH="${1:-Drishti}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
PORT="${GARUDA_PORT:-8080}"
cd "$PROJECT_DIR"

if [ "$BRANCH" = "--check" ]; then
  exec python3 "$SCRIPT_DIR/check_deploy.py"
fi

echo "[deploy] $PROJECT_DIR is on $(git rev-parse --abbrev-ref HEAD) at $(git rev-parse --short HEAD)"
if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
  echo "[deploy] this checkout has local edits; nothing changed. See them with: git status" >&2
  exit 1
fi

git fetch origin "$BRANCH"
git checkout "$BRANCH"
git merge --ff-only "origin/$BRANCH"
WANT="$(git rev-parse --short HEAD)"
echo "[deploy] checkout is now $BRANCH at $WANT: $(git log -1 --format=%s)"

echo "[deploy] restarting garuda-web"
sudo systemctl restart garuda-web

for _ in $(seq 1 60); do
  if HEALTH="$(curl -sf "http://127.0.0.1:${PORT}/api/health")"; then break; fi
  sleep 2
done
if [ -z "${HEALTH:-}" ]; then
  echo "[deploy] the service did not answer on port ${PORT}. Look at: journalctl -u garuda-web -n 50" >&2
  exit 1
fi

LIVE="$(printf '%s' "$HEALTH" | python3 -c 'import json, sys; print(json.load(sys.stdin).get("commit", "?"))')"
if [ "${LIVE:0:7}" = "${WANT:0:7}" ]; then
  echo "[deploy] live: commit $LIVE. Checking every fix, here and through the public site:"
  if ! python3 "$SCRIPT_DIR/check_deploy.py" --commit "$WANT"; then
    echo "[deploy] some fixes are not live: see FAIL above, and send that output over." >&2
    exit 1
  fi
  echo "[deploy] Close and reopen the site's tab to load the new pages."
else
  echo "[deploy] the service reports commit $LIVE, the checkout is $WANT: it is not running this checkout." >&2
  echo "[deploy] Check its WorkingDirectory with: systemctl cat garuda-web" >&2
  exit 1
fi
