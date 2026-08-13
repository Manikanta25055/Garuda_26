#!/bin/bash
# Build the Drishti bundle and put it where FastAPI serves it.
#
# Runs on the Pi. npm comes from corepack in ~/.local/bin — there is no system
# npm package installed, so PATH has to include it.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
export PATH="$HOME/.local/bin:$PATH"
export COREPACK_ENABLE_DOWNLOAD_PROMPT=0

echo "[drishti] building in $PROJECT_DIR/drishti_web"
cd "$PROJECT_DIR/drishti_web"
npm ci
npm test
npm run build

if [ ! -f "$PROJECT_DIR/basic_pipelines/drishti_dist/index.html" ]; then
  echo "[drishti] build produced no index.html — refusing to restart" >&2
  exit 1
fi

echo "[drishti] seeding devices if the registry is empty"
python3 "$PROJECT_DIR/scripts/seed_drishti_devices.py"

echo "[drishti] restarting server"
# systemd owns this process (garuda-web.service). restart_server.sh pkills and
# relaunches by hand, which fights the unit's Restart= policy and leaves two
# candidates for port 8080.
sudo systemctl restart garuda-web
until curl -sf -o /dev/null http://127.0.0.1:8080/; do sleep 2; done
echo "[drishti] done → https://drishti.veeramanikanta.in"
