# Connecting to Garuda Web UI

## Option 1 — Same WiFi (easiest)

Find the RPi5's local IP:
```bash
hostname -I   # run on the RPi5
# e.g. 192.168.1.45
```

Then on your Mac, open:
```
http://192.168.1.45:8080
```

That's it. You're hitting the FastAPI server directly — no Vercel involved.

---

## Option 2 — From anywhere (Cloudflare Tunnel, free)

Run this **on the RPi5**:
```bash
# Install (one time)
curl -L https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-arm64 -o cloudflared
chmod +x cloudflared
sudo mv cloudflared /usr/local/bin/

# Start tunnel (no account needed)
cloudflared tunnel --url http://localhost:8080
```

It prints a public URL like `https://random-words-here.trycloudflare.com`. Open that on your Mac.

---

## Option 3 — From Vercel frontend

If you open `garuda.veeramanikanta.in` (Vercel) on your Mac, click **Configure** (bottom-right corner of the login screen) and paste either the local IP or the Cloudflare URL. The frontend will connect to your RPi5 backend from there.

---

**Recommended:** Option 1 for home use (fastest, zero latency). Option 2 when you're away from home.
