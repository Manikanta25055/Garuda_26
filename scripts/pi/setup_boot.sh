#!/bin/bash
# Installs everything Drishti needs to come back by itself after a reboot or
# power cut. Safe to run again. Usage: sudo scripts/pi/setup_boot.sh
#   - garuda-web.service (website), with the Hailo wake-up helper
#   - cloudflared drop-in (tunnel keeps retrying at boot)
#   - claude-rc.service (claude remote-control in tmux "claude", in ~/Projects)
#   - labwc touch mapping to DSI-2 (so the screen rotation applies to touch)
#   - linger for manikanta (keeps /run/user/1000 for PipeWire)
#   - journal capped at 200 MB, vm.swappiness=10
set -euo pipefail
[ "$(id -u)" -eq 0 ] || exec sudo "$0" "$@"
here="$(cd "$(dirname "$0")" && pwd)"

install -m 644 "$here/garuda-web.service" /etc/systemd/system/garuda-web.service
install -m 755 "$here/drishti-hailo-recover" /usr/local/sbin/drishti-hailo-recover
install -d /etc/systemd/system/cloudflared.service.d
install -m 644 "$here/cloudflared-override.conf" /etc/systemd/system/cloudflared.service.d/override.conf
install -m 644 "$here/claude-rc.service" /etc/systemd/system/claude-rc.service
install -d /etc/systemd/journald.conf.d
install -m 644 "$here/journald-drishti.conf" /etc/systemd/journald.conf.d/drishti.conf
install -m 644 "$here/sysctl-drishti.conf" /etc/sysctl.d/99-drishti.conf

# The old touch calibration rule mis-mapped touch; make sure it is gone.
if [ -e /etc/udev/rules.d/99-drishti-touch.rules ]; then
  rm -f /etc/udev/rules.d/99-drishti-touch.rules
  udevadm control --reload
fi

# labwc only rotates touch with the screen when the touch device is mapped to
# the output. The Pi's autotouch helper wrote the old kernel device name
# ("11-005d Goodix ..."), which no longer matches, so add a name-less <touch>
# entry: it applies to any touchscreen.
rc=/home/manikanta/.config/labwc/rc.xml
ns=http://openbox.org/3.4/rc
if [ -f "$rc" ] && ! sudo -u manikanta xmlstarlet sel -N o=$ns -t -c '//o:touch[not(@deviceName)]' "$rc" | grep -q touch; then
  sudo -u manikanta cp -p "$rc" "$rc.bak-drishti-touch"
  sudo -u manikanta sh -c "xmlstarlet ed -N o=$ns -s /o:openbox_config -t elem -n touch \
    -i '/o:openbox_config/touch[last()]' -t attr -n mapToOutput -v DSI-2 \
    -i '/o:openbox_config/touch[last()]' -t attr -n mouseEmulation -v yes \
    '$rc.bak-drishti-touch' > '$rc'"
  pkill -HUP -x labwc || true
fi

systemctl daemon-reload
systemctl enable garuda-web.service cloudflared.service claude-rc.service
loginctl enable-linger manikanta
systemctl restart systemd-journald
sysctl -q -p /etc/sysctl.d/99-drishti.conf

echo "Installed. claude-rc starts at next boot (or now: sudo systemctl restart claude-rc)."
