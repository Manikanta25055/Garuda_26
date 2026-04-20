#!/bin/bash
# systemd wrapper: source TAPPAS env, then exec Garuda_web.py
set -e
cd /home/manikanta/Projects/Garuda_26
# shellcheck disable=SC1091
source /home/manikanta/Projects/Garuda_26/setup_env.sh
exec python3 /home/manikanta/Projects/Garuda_26/basic_pipelines/Garuda_web.py
