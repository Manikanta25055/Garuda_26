# Garuda — Full Setup & Operations Guide

## What is running where

| Service | URL | Description |
|---------|-----|-------------|
| Garuda Web UI | `https://api.veeramanikanta.in` | Full UI + camera feed + API, served from RPi5 via Cloudflare Tunnel |
| Cloudflare Tunnel | `api.veeramanikanta.in → localhost:8080` | Gives public HTTPS access to RPi5 |

---

## Credentials

| Account | Username | Where |
|---------|----------|-------|
| Garuda admin | `admin` | Login screen → Admin card → OTP sent to alert email |
| Cloudflare | manikanta@... | dash.cloudflare.com |

---

## LED status indicators (ACT green LED on RPi5)

| LED state | Meaning |
|-----------|---------|
| Blinking (heartbeat) | Booted, waiting for WiFi |
| Solid green | WiFi connected, Garuda running |
| Normal activity blink | Garuda exited / restarting |

---

## Daily use — everything auto-starts on boot

Both services start automatically when RPi5 powers on. Just open:
```
https://api.veeramanikanta.in
```

To check if services are running:
```bash
sudo systemctl status garuda
sudo systemctl status cloudflared
```

---

## Starting / stopping manually

```bash
# Garuda Web UI
sudo systemctl start garuda
sudo systemctl stop garuda
sudo systemctl restart garuda

# Cloudflare Tunnel
sudo systemctl start cloudflared
sudo systemctl stop cloudflared
sudo systemctl restart cloudflared
```

---

## Viewing live logs

```bash
# Garuda logs (camera, detections, errors)
sudo journalctl -u garuda -f

# Cloudflare tunnel logs
sudo journalctl -u cloudflared -f

# Last 50 lines without following
sudo journalctl -u garuda -n 50 --no-pager
```

---

## Running Garuda manually (for development/testing)

```bash
cd /home/manikanta/Projects/hailo-rpi5-examples
source setup_env.sh
python3 basic_pipelines/Garuda_web.py --input rpi
```

> Stop the systemd service first or you'll get "port 8080 already in use":
> ```bash
> sudo systemctl stop garuda
> ```

---

## Hailo device troubleshooting

**"Address already in use" on port 8080:**
```bash
sudo lsof -i :8080
sudo kill -9 <PID>
```

**"Not enough free devices" (Hailo device locked):**
```bash
sudo lsof /dev/hailo0
sudo kill -9 <PID>
```

**Hailo driver not loaded after kernel update:**
```bash
lsmod | grep hailo
sudo dkms install hailo_pci/4.20.0 -k $(uname -r)
sudo modprobe hailo_pci
ls -la /dev/hailo0
```

---

## Cloudflare Tunnel — full setup (one-time, already done)

If you ever need to redo this from scratch:

**1. Install cloudflared:**
```bash
curl -L https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-arm64 \
  -o cloudflared
chmod +x cloudflared
sudo mv cloudflared /usr/local/bin/
```

**2. Login (headless fix for RPi5):**
```bash
cloudflared tunnel login
# Copy the URL it prints → open on Mac browser → authorize
# After authorizing, Mac browser redirects to localhost:PORT/callback?code=...
# Copy that redirect URL from Mac address bar, then on RPi5:
curl "http://localhost:PORT/callback?code=...&state=..."
```

**3. Create tunnel:**
```bash
cloudflared tunnel create garuda-api
# Note the tunnel ID it prints
```

**4. Route DNS:**
```bash
cloudflared tunnel route dns garuda-api api.veeramanikanta.in
```

**5. Create config (copy to /etc/cloudflared/ for system service):**
```bash
sudo mkdir -p /etc/cloudflared
sudo nano /etc/cloudflared/config.yml
```
```yaml
tunnel: garuda-api
credentials-file: /etc/cloudflared/a6565009-b3d2-4db3-a5bc-51443bcbc0e2.json

ingress:
  - hostname: api.veeramanikanta.in
    service: http://localhost:8080
  - service: http_status:404
```

**6. Copy credentials to system location:**
```bash
sudo cp ~/.cloudflared/cert.pem /etc/cloudflared/
sudo cp ~/.cloudflared/a6565009-b3d2-4db3-a5bc-51443bcbc0e2.json /etc/cloudflared/
```

**7. Install and enable as service:**
```bash
sudo cloudflared service install
sudo systemctl enable cloudflared
sudo systemctl start cloudflared
```

---

## Garuda systemd service — full setup (one-time, already done)

**Startup script** at `/home/manikanta/start_garuda.sh`:
```bash
#!/bin/bash

# Wait for WiFi/internet before starting
echo "[Garuda] Waiting for network connection..."
ATTEMPT=0
while true; do
    if ping -c 1 -W 3 8.8.8.8 &>/dev/null; then
        echo "[Garuda] Network connected."
        break
    fi
    ATTEMPT=$((ATTEMPT + 1))
    echo "[Garuda] No network (attempt $ATTEMPT). Retrying in 15 seconds..."
    sleep 15
done

cd /home/manikanta/Projects/hailo-rpi5-examples
source setup_env.sh
python3 basic_pipelines/Garuda_web.py --input rpi
```

**Service file** at `/etc/systemd/system/garuda.service`:
```ini
[Unit]
Description=Garuda AI Security System
After=network.target

[Service]
Type=simple
User=manikanta
WorkingDirectory=/home/manikanta/Projects/hailo-rpi5-examples
ExecStart=/bin/bash /home/manikanta/start_garuda.sh
Restart=on-failure
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

**Enable:**
```bash
sudo systemctl daemon-reload
sudo systemctl enable garuda
sudo systemctl start garuda
```

---

## DNS setup (Cloudflare dashboard — veeramanikanta.in)

| Type | Name | Target | Proxy |
|------|------|--------|-------|
| CNAME | api | `a6565009-b3d2-4db3-a5bc-51443bcbc0e2.cfargotunnel.com` | ON |

Nameservers in BigRock are set to Cloudflare's nameservers.

---

## Updating Garuda code

```bash
cd /home/manikanta/Projects/hailo-rpi5-examples
git pull
sudo systemctl restart garuda
```

---

## Connecting from local network (no internet needed)

```bash
hostname -I   # find RPi5's IP
```
Then open `http://192.168.1.8:8080` directly in browser (same WiFi only).
