#!/bin/bash
# systemd wrapper: source TAPPAS env, then exec Garuda_web.py
set -e
cd /home/manikanta/Projects/Garuda_26

# Straight after power on, the Hailo PCIe chip can take a while to answer.
# setup_env.sh asks it for its architecture and gives up if it does not, which
# used to stop the whole service at boot. Wait for it first (up to two
# minutes); if it never answers, exit and let systemd try again.
for i in $(seq 1 60); do
  if hailortcli fw-control identify 2>/dev/null | grep -q "Device Architecture"; then
    break
  fi
  if [ "$i" -eq 60 ]; then
    echo "[garuda-web] the Hailo chip did not answer in 120 s; systemd will retry" >&2
    exit 1
  fi
  sleep 2
done

# shellcheck disable=SC1091
source /home/manikanta/Projects/Garuda_26/setup_env.sh
# systemd gives no session environment. Without the runtime dir, parec (the
# camera's live sound, /ws/audio) cannot reach PipeWire's Pulse server and
# gets nothing from the USB microphone.
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
exec python3 /home/manikanta/Projects/Garuda_26/basic_pipelines/Garuda_web.py
