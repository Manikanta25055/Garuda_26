#!/bin/bash
# Installs everything Drishti needs to come back by itself after a reboot or
# power cut. Safe to run again. Usage: sudo scripts/pi/setup_boot.sh
#   - garuda-web.service (website), with the Hailo wake-up helper
#   - cloudflared drop-in (tunnel keeps retrying at boot)
#   - touch calibration udev rule
#   - claude-rc.service (claude remote-control in tmux "claude")
#   - linger for manikanta (keeps /run/user/1000 for PipeWire)
set -euo pipefail
[ "$(id -u)" -eq 0 ] || exec sudo "$0" "$@"
here="$(cd "$(dirname "$0")" && pwd)"

install -m 644 "$here/garuda-web.service" /etc/systemd/system/garuda-web.service
install -m 755 "$here/drishti-hailo-recover" /usr/local/sbin/drishti-hailo-recover
install -d /etc/systemd/system/cloudflared.service.d
install -m 644 "$here/cloudflared-override.conf" /etc/systemd/system/cloudflared.service.d/override.conf
install -m 644 "$here/claude-rc.service" /etc/systemd/system/claude-rc.service
install -m 644 "$here/99-drishti-touch.rules" /etc/udev/rules.d/99-drishti-touch.rules

systemctl daemon-reload
systemctl enable garuda-web.service cloudflared.service claude-rc.service
loginctl enable-linger manikanta
udevadm control --reload
udevadm trigger --subsystem-match=input --action=change
udevadm settle

echo "Installed. Touch change applies after logging out of the desktop or a reboot."
echo "claude-rc starts at next boot (or now: sudo systemctl start claude-rc)."
