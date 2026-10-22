#!/bin/bash
# systemd wrapper: source TAPPAS env, then exec Garuda_web.py
set -e
cd /home/manikanta/Projects/Garuda_26
# shellcheck disable=SC1091
source /home/manikanta/Projects/Garuda_26/setup_env.sh
# systemd gives no session environment. Without the runtime dir, PyAudio
# (Narada's voice loop) cannot reach PipeWire and opens the USB microphone's
# ALSA device directly, which locks everyone else out of it: the camera's live
# sound (/ws/audio) then gets nothing. Through PipeWire both share it.
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
exec python3 /home/manikanta/Projects/Garuda_26/basic_pipelines/Garuda_web.py
