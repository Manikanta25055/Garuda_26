"""
generate_architecture.py
Generates garuda_architecture.drawio — full system architecture diagram.
Run: python3 generate_architecture.py
Open: garuda_architecture.drawio in draw.io (app.diagrams.net)
"""

import xml.etree.ElementTree as ET

# ── ID counter ────────────────────────────────────────────────────────────────
_id = 2
def nid():
    global _id
    v = str(_id)
    _id += 1
    return v

# ── Style presets ─────────────────────────────────────────────────────────────
def box(fill, stroke, font_size=11, bold=False, rounded=1, dashed=0):
    b = "1" if bold else "0"
    return (
        f"rounded={rounded};whiteSpace=wrap;html=1;"
        f"fillColor={fill};strokeColor={stroke};"
        f"fontSize={font_size};fontStyle={b};"
        f"dashed={dashed};"
    )

def container_style(fill, stroke):
    return (
        f"swimlane;startSize=28;fillColor={fill};strokeColor={stroke};"
        f"fontStyle=1;fontSize=12;whiteSpace=wrap;html=1;"
    )

def cylinder(fill, stroke):
    return (
        f"shape=mxgraph.cisco.storage.storage;whiteSpace=wrap;html=1;"
        f"fillColor={fill};strokeColor={stroke};fontSize=10;"
    )

def db_style(fill, stroke):
    return (
        f"shape=cylinder3;whiteSpace=wrap;html=1;"
        f"fillColor={fill};strokeColor={stroke};fontSize=10;"
    )

def edge_style(label="", dashed=0, color="#555555", end_arrow="block"):
    return (
        f"edgeStyle=orthogonalEdgeStyle;html=1;exitX=0.5;exitY=1;exitDx=0;exitDy=0;"
        f"entryX=0.5;entryY=0;entryDx=0;entryDy=0;"
        f"dashed={dashed};strokeColor={color};endArrow={end_arrow};endFill=1;"
    )

def edge_h(dashed=0, color="#555555"):
    return (
        f"edgeStyle=orthogonalEdgeStyle;html=1;exitX=1;exitY=0.5;exitDx=0;exitDy=0;"
        f"entryX=0;entryY=0.5;entryDx=0;entryDy=0;"
        f"dashed={dashed};strokeColor={color};endArrow=block;endFill=1;"
    )

# ── Colours ───────────────────────────────────────────────────────────────────
HW       = ("#dae8fc", "#6c8ebf")   # blue
PIPE     = ("#d5e8d4", "#82b366")   # green
DETECT   = ("#fff2cc", "#d6b656")   # yellow
ALERT    = ("#f8cecc", "#b85450")   # red
API      = ("#e1d5e7", "#9673a6")   # purple
IFACE    = ("#ffe6cc", "#d6a050")   # orange
STORE    = ("#f5f5f5", "#666666")   # grey
SVC      = ("#dae8fc", "#6c8ebf")   # blue (services)
SEC      = ("#fde9d9", "#d79b00")   # amber (security)
NARADA   = ("#e6f3ff", "#4da3e8")   # light blue
HAILO    = ("#d5e8d4", "#82b366")   # green
VOICE_CLR= ("#e6f3ff", "#4da3e8")

# ── Builder helpers ───────────────────────────────────────────────────────────
root_el = None
cells = []

def cell(label, x, y, w, h, style, vertex=1, parent="1", id_=None):
    i = id_ or nid()
    c = ET.Element("mxCell", id=i, value=label, style=style,
                   vertex=str(vertex), parent=parent)
    ET.SubElement(c, "mxGeometry", x=str(x), y=str(y),
                  width=str(w), height=str(h), **{"as": "geometry"})
    cells.append(c)
    return i

def container(label, x, y, w, h, fill, stroke, id_=None):
    i = id_ or nid()
    s = container_style(fill, stroke)
    c = ET.Element("mxCell", id=i, value=label, style=s,
                   vertex="1", parent="1")
    ET.SubElement(c, "mxGeometry", x=str(x), y=str(y),
                  width=str(w), height=str(h), **{"as": "geometry"})
    cells.append(c)
    return i

def child(label, x, y, w, h, style, parent_id):
    i = nid()
    c = ET.Element("mxCell", id=i, value=label, style=style,
                   vertex="1", parent=parent_id)
    ET.SubElement(c, "mxGeometry", x=str(x), y=str(y),
                  width=str(w), height=str(h), **{"as": "geometry"})
    cells.append(c)
    return i

def connect(src, tgt, label="", style=None, parent="1"):
    i = nid()
    s = style or edge_style()
    c = ET.Element("mxCell", id=i, value=label, style=s,
                   edge="1", source=src, target=tgt, parent=parent)
    ET.SubElement(c, "mxGeometry", relative="1", **{"as": "geometry"})
    cells.append(c)
    return i

# ══════════════════════════════════════════════════════════════════════════════
# LAYOUT — canvas 2100 × 1900
# ══════════════════════════════════════════════════════════════════════════════

# ── BAND 0: Title ─────────────────────────────────────────────────────────────
cell(
    "Garuda — Full System Architecture\n"
    "Raspberry Pi 5 (16 GB)  ·  Hailo-8L NPU (13 TOPS)  ·  Sony IMX708 @ 60 fps",
    30, 10, 1480, 50,
    "text;html=1;align=center;verticalAlign=middle;fontSize=14;fontStyle=1;"
    "fillColor=none;strokeColor=none;"
)

# ══════════════════════════════════════════════════════════════════════════════
# BAND 1: HARDWARE LAYER  y=80
# ══════════════════════════════════════════════════════════════════════════════
hw_cont = container("HARDWARE LAYER", 30, 80, 1060, 90, HW[0], HW[1])

cam     = child("Sony IMX708\n1280×720 @ 60 fps", 20,  30, 150, 45, box(HW[0], HW[1], bold=True), hw_cont)
mic     = child("Microphone\n(Voice Input)",        200, 30, 130, 45, box(HW[0], HW[1]), hw_cont)
hcsr    = child("HC-SR04\nUltrasonic (GPIO18/24)",  360, 30, 150, 45, box(HW[0], HW[1]), hw_cont)
led     = child("LED Indicator\n(GPIO17)",           540, 30, 130, 45, box(HW[0], HW[1]), hw_cont)
net_hw  = child("LAN / WiFi\n(ARP Network)",        700, 30, 130, 45, box(HW[0], HW[1]), hw_cont)
hailo_hw= child("Hailo-8L NPU\n13 TOPS  PCIe",      860, 30, 150, 45, box(HAILO[0], HAILO[1], bold=True), hw_cont)

# ══════════════════════════════════════════════════════════════════════════════
# BAND 2: GSTREAMER AI PIPELINE  y=210
# ══════════════════════════════════════════════════════════════════════════════
pipe_cont = container("GSTREAMER AI PIPELINE  (Raspberry Pi 5 — On-Device)", 30, 210, 1060, 220, PIPE[0], PIPE[1])

# pipeline nodes inside container (relative x,y)
p_src    = child("libcamerasrc\n(IMX708 driver)", 15,  50, 120, 45, box(PIPE[0], PIPE[1]), pipe_cont)
p_scale  = child("videoscale\nvideoscale",        160, 50, 100, 45, box(PIPE[0], PIPE[1]), pipe_cont)
p_conv   = child("videoconvert\n(RGB 640×640)",   285, 50, 120, 45, box(PIPE[0], PIPE[1]), pipe_cont)
p_tee    = child("tee",                           430, 70,  60, 30, box(PIPE[0], PIPE[1], bold=True), pipe_cont)

# Branch A — bypass
p_bypass = child("bypass_queue\n(raw frames)",    515, 40,  120, 45, box(PIPE[0], PIPE[1]), pipe_cont)

# Branch B — Hailo inference
p_hnet   = child("hailonet\n(YOLOv8s / YOLOv6n HEF)\nBatch=1", 515, 100, 160, 50, box(HAILO[0], HAILO[1], bold=True), pipe_cont)
p_hfilt  = child("hailofilter\nNMS  (score≥0.25, IOU≤0.45)\nlibyolo_hailortpp_post.so", 700, 100, 180, 50, box(HAILO[0], HAILO[1]), pipe_cont)

# Mux and callback
p_mux    = child("hailomuxer\n(sync raw + inference)", 700, 40, 160, 45, box(PIPE[0], PIPE[1], bold=True), pipe_cont)
p_cb     = child("identity_callback\n(app_callback pad probe)", 885, 40, 160, 45, box(DETECT[0], DETECT[1], bold=True), pipe_cont)
p_sink   = child("fakesink\n(no display)", 1000, 145, 50, 30, box(PIPE[0], PIPE[1], font_size=9), pipe_cont)

# ── internal pipeline edges ───────────────────────────────────────────────────
h = edge_h()
connect(p_src, p_scale, style=h, parent=pipe_cont)
connect(p_scale, p_conv, style=h, parent=pipe_cont)
connect(p_conv, p_tee, style=h, parent=pipe_cont)
connect(p_tee, p_bypass, label="bypass", style=h, parent=pipe_cont)
connect(p_tee, p_hnet, label="inference", style=h, parent=pipe_cont)
connect(p_hnet, p_hfilt, style=h, parent=pipe_cont)
connect(p_bypass, p_mux, style=h, parent=pipe_cont)
connect(p_hfilt, p_mux, style=edge_h(color="#82b366"), parent=pipe_cont)
connect(p_mux, p_cb, style=h, parent=pipe_cont)
connect(p_cb, p_sink, style=edge_h(color="#aaaaaa"), parent=pipe_cont)

# hardware → pipeline
connect(cam,  p_src,  style=edge_style(color=HW[1]))
connect(hailo_hw, p_hnet, style=edge_style(color=HAILO[1]))

# ══════════════════════════════════════════════════════════════════════════════
# BAND 3: DETECTION & DECISION LOGIC  y=470
# ══════════════════════════════════════════════════════════════════════════════
det_cont = container("DETECTION & DECISION LOGIC", 30, 470, 1060, 170, DETECT[0], DETECT[1])

d_frame  = child("Frame Analysis\n• Camera blindness (var < 50, 300 frames)\n• JPEG encode → MJPEG buffer\n• WebRTC raw BGR frame", 15, 35, 195, 75, box(DETECT[0], DETECT[1]), det_cont)
d_parse  = child("Detection Parser\n• hailo.get_roi_from_buffer()\n• get_objects_typed(HAILO_DETECTION)\n• conf ≥ threshold (default 0.35)", 235, 35, 200, 75, box(DETECT[0], DETECT[1]), det_cont)
d_priv   = child("Privacy Mode\nGaussian Blur (51×51, σ=30)\non person bounding boxes", 460, 35, 170, 75, box(DETECT[0], DETECT[1]), det_cont)
d_danger = child("Danger Check\nlabel == DANGER_LABEL\nAND ≥2 consecutive frames\n→ ALERT TRIGGERED", 655, 35, 175, 75, box(ALERT[0], ALERT[1], bold=True), det_cont)
d_watch  = child("Watch Labels\nperson / backpack / suitcase\nSilent log (30 s cooldown)", 855, 35, 160, 75, box(DETECT[0], DETECT[1]), det_cont)

connect(p_cb, d_frame,  style=edge_style(color=DETECT[1]))
connect(d_frame,  d_parse,  style=edge_h(), parent=det_cont)
connect(d_parse,  d_priv,   style=edge_h(), parent=det_cont)
connect(d_parse,  d_danger, style=edge_h(), parent=det_cont)
connect(d_parse,  d_watch,  style=edge_h(), parent=det_cont)

# ══════════════════════════════════════════════════════════════════════════════
# BAND 4: ALERT ENGINE  y=680
# ══════════════════════════════════════════════════════════════════════════════
alt_cont = container("ALERT ENGINE", 30, 680, 1060, 110, ALERT[0], ALERT[1])

a_soft   = child("trigger_software_alert()\n• _alert_active = True\n• 3 s timer extension per frame", 15,  35, 195, 55, box(ALERT[0], ALERT[1]), alt_cont)
a_email  = child("send_email_alert()\n• Gmail SMTP-SSL port 465\n• 60 s cooldown\n• Subject: NORMAL / HIGH / EMERGENCY", 235, 35, 200, 55, box(ALERT[0], ALERT[1]), alt_cont)
a_gpio   = child("GPIO LED\n(GPIO17)\nLights on alert", 460, 35, 120, 55, box(ALERT[0], ALERT[1]), alt_cont)
a_ws     = child("WebSocket Push\npush_urgent_ws()\n→ all connected clients", 605, 35, 160, 55, box(ALERT[0], ALERT[1]), alt_cont)
a_tamper = child("TAMPER ALERTS\n• Camera blindness\n• Dead man's switch\n  (bypass DND/Idle)", 790, 35, 175, 55, box("#ff0000", "#cc0000", bold=True), alt_cont)

connect(d_danger, a_soft,   style=edge_style(color=ALERT[1]))
connect(a_soft,   a_email,  style=edge_h(color=ALERT[1]), parent=alt_cont)
connect(a_soft,   a_gpio,   style=edge_h(color=ALERT[1]), parent=alt_cont)
connect(a_soft,   a_ws,     style=edge_h(color=ALERT[1]), parent=alt_cont)
connect(a_email,  led,      label="GPIO",  style=edge_style(dashed=1, color="#aaaaaa"))

# ══════════════════════════════════════════════════════════════════════════════
# BAND 5: FASTAPI BACKEND + SECURITY  y=830
# ══════════════════════════════════════════════════════════════════════════════
api_cont = container("FASTAPI BACKEND  (uvicorn · 127.0.0.1:8080 · Cloudflare Tunnel → garuda.veeramanikanta.in)", 30, 830, 1060, 200, API[0], API[1])

api_auth = child("Authentication\n• POST /api/login (user, PBKDF2)\n• POST /api/admin/send-otp\n• POST /api/admin/verify-otp (2FA)\n• POST /api/forgot/send-otp\n• POST /api/forgot/reset", 15, 35, 200, 110, box(SEC[0], SEC[1]), api_cont)
api_sess = child("Session Management\n• Access token: 15 min\n• Refresh token: 7 days\n• secrets.token_hex(64)\n• Master key (8 h)\n• Session invalidation on pw change", 235, 35, 190, 110, box(SEC[0], SEC[1]), api_cont)
api_rest = child("REST API\n• GET  /api/state\n• POST /api/modes\n• GET  /api/users\n• POST /api/users/add|delete|update\n• POST /api/config\n• GET  /api/logs/*\n• POST /api/presence/devices", 445, 35, 185, 110, box(API[0], API[1]), api_cont)
api_rt   = child("Real-time Streaming\n• WS  /ws  (state push 2 s)\n• GET  /stream  (MJPEG)\n• POST /api/webrtc/offer (H.264)\n• POST /api/chat/stream (SSE)", 650, 35, 185, 110, box(API[0], API[1]), api_cont)
api_mid  = child("Security Middleware\n• Rate limit: 30 req/60 s/IP\n• Brute-force lockout: 5 fail→5 min\n• HSTS · CSP · X-Frame-Options\n• CORS (known origins only)", 855, 35, 190, 110, box(SEC[0], SEC[1]), api_cont)

connect(a_ws, api_rt, style=edge_style(color=API[1]))

# ══════════════════════════════════════════════════════════════════════════════
# BAND 6: INTERFACES  y=1070
# ══════════════════════════════════════════════════════════════════════════════
iface_cont = container("USER INTERFACES", 30, 1070, 1060, 120, IFACE[0], IFACE[1])

if_web   = child("Web PWA\n(garuda.veeramanikanta.in)\nVanilla JS · MJPEG/WebRTC\nService Worker · Offline cache", 15,  35, 200, 65, box(IFACE[0], IFACE[1], bold=True), iface_cont)
if_mac   = child("macOS Native App\n(Swift)\nWelcome/Login/Dashboard/Alerts\nHTTP + WebSocket client", 240, 35, 200, 65, box(IFACE[0], IFACE[1]), iface_cont)
if_voice = child("Narada — Voice Assistant\nGoogle Speech Recognition\n→ Groq LLM (llama-3.3-70b)\n→ Rule-based fallback", 465, 35, 200, 65, box(NARADA[0], NARADA[1]), iface_cont)
if_email = child("Email Alerts\nGmail SMTP-SSL\nDanger / Tamper / Heartbeat\n(recipient configurable)", 700, 35, 190, 65, box(ALERT[0], ALERT[1]), iface_cont)
if_phone = child("Phone Presence\n(ARP scan /proc/net/arp)\nKnown device MAC match\n30 s poll · 90 s grace", 915, 35, 130, 65, box(SVC[0], SVC[1]), iface_cont)

connect(api_rest, if_web,   style=edge_style(color=API[1]))
connect(api_rest, if_mac,   style=edge_style(color=API[1]))
connect(api_rest, if_voice, style=edge_style(color=API[1]))
connect(mic, if_voice, style=edge_style(dashed=1, color="#aaaaaa"))
connect(net_hw, if_phone, style=edge_style(dashed=1, color=SVC[1]))

# ══════════════════════════════════════════════════════════════════════════════
# BAND 7: STORAGE LAYER  y=1230
# ══════════════════════════════════════════════════════════════════════════════
store_cont = container("STORAGE LAYER  (On-Device — Raspberry Pi SD Card)", 30, 1230, 1060, 130, STORE[0], STORE[1])

st_sqlite = child("SQLite\ngaruda_events.db\n• Event queue (offline-resilient)\n• synced flag\n• Indexed by ts", 15,  35, 180, 75, db_style(STORE[0], STORE[1]), store_cont)
st_json   = child("JSON Files\n• users.json  (PBKDF2 hashes)\n• config.json  (modes, schedule)\n• alert_history.json\n• presence_log.json\n• master_keys.json", 215, 35, 185, 75, box(STORE[0], STORE[1]), store_cont)
st_log    = child("Text Logs  (RAM-buffered → flush 60 s)\n• perm_system_log.txt\n• perm_detection_log.txt\n• perm_voice_log.txt\n• danger_sightings.txt\nRotate at 10 MB", 420, 35, 200, 75, box(STORE[0], STORE[1]), store_cont)
st_clips  = child("Video Clips\n• OpenCV mp4 (60 s max)\n• AES-256-GCM encrypted\n• SFTP upload via paramiko\n• Plaintext deleted after upload", 640, 35, 185, 75, box(STORE[0], STORE[1]), store_cont)
st_atomic = child("Atomic JSON Write\ntmpfile → fsync → os.replace\n(prevents corruption on\npower loss)", 845, 35, 185, 55, box(STORE[0], STORE[1], font_size=10), store_cont)

connect(d_watch,  st_sqlite, style=edge_style(dashed=1, color=STORE[1]))
connect(d_danger, st_sqlite, style=edge_style(dashed=1, color=STORE[1]))
connect(d_frame,  st_log,    style=edge_style(dashed=1, color=STORE[1]))

# ══════════════════════════════════════════════════════════════════════════════
# RIGHT COLUMN: BACKGROUND SERVICES  x=1120, y=210
# ══════════════════════════════════════════════════════════════════════════════
svc_cont = container("BACKGROUND SERVICES\n(daemon threads)", 1120, 210, 200, 580, SVC[0], SVC[1])

svc_pres  = child("Presence Poller\nUDP probe → ARP scan\n/proc/net/arp\nEvery 30 s", 10, 40, 175, 55, box(SVC[0], SVC[1]), svc_cont)
svc_dead  = child("Dead Man's Switch\nGET /api/heartbeat\nNo ping in 180 s\n→ TAMPER email", 10, 110, 175, 55, box(ALERT[0], ALERT[1]), svc_cont)
svc_conn  = child("Connectivity Monitor\nDNS probe 1.1.1.1\nEvery 30 s\nLogs online/offline", 10, 180, 175, 55, box(SVC[0], SVC[1]), svc_cont)
svc_sched = child("Schedule Monitor\nHH:MM mode transitions\nEvery 30 s\n(DND / Night / Idle…)", 10, 250, 175, 55, box(SVC[0], SVC[1]), svc_cont)
svc_flush = child("Log Flush Thread\nRAM buffer → disk\nEvery 60 s\nReduces SD card wear", 10, 320, 175, 55, box(SVC[0], SVC[1]), svc_cont)
svc_prune = child("Session Pruner\nRemove expired tokens\nEvery WS broadcast cycle", 10, 390, 175, 45, box(SVC[0], SVC[1], font_size=10), svc_cont)
svc_ws    = child("WS Broadcaster\nasync task\nPush every 2 s +\nevent-driven (urgent)", 10, 450, 175, 55, box(API[0], API[1]), svc_cont)

connect(svc_pres, if_phone, style=edge_h(color=SVC[1]))
connect(svc_flush, st_log,  style=edge_h(dashed=1, color=STORE[1]))
connect(svc_ws,    api_rt,  style=edge_h(color=API[1]))

# ══════════════════════════════════════════════════════════════════════════════
# LEGEND  x=1120, y=830
# ══════════════════════════════════════════════════════════════════════════════
leg_cont = container("LEGEND", 1120, 830, 200, 430, "#ffffff", "#666666")

child("Hardware / Input",         10, 35,  175, 30, box(HW[0],     HW[1]),     leg_cont)
child("GStreamer Pipeline",        10, 75,  175, 30, box(PIPE[0],   PIPE[1]),   leg_cont)
child("Detection Logic",           10, 115, 175, 30, box(DETECT[0], DETECT[1]), leg_cont)
child("Alert Engine",              10, 155, 175, 30, box(ALERT[0],  ALERT[1]),  leg_cont)
child("Security / Auth",           10, 195, 175, 30, box(SEC[0],    SEC[1]),    leg_cont)
child("API / Streaming",           10, 235, 175, 30, box(API[0],    API[1]),    leg_cont)
child("User Interfaces",           10, 275, 175, 30, box(IFACE[0],  IFACE[1]),  leg_cont)
child("Storage",                   10, 315, 175, 30, box(STORE[0],  STORE[1]),  leg_cont)
child("Background Services",       10, 355, 175, 30, box(SVC[0],    SVC[1]),    leg_cont)

# ══════════════════════════════════════════════════════════════════════════════
# ASSEMBLE XML
# ══════════════════════════════════════════════════════════════════════════════
graph = ET.Element("mxGraphModel",
    dx="1422", dy="762", grid="1", gridSize="10",
    guides="1", tooltips="1", connect="1", arrows="1",
    fold="1", page="1", pageScale="1",
    pageWidth="1654", pageHeight="2338",   # A1 landscape
    math="0", shadow="1"
)
root = ET.SubElement(graph, "root")
ET.SubElement(root, "mxCell", id="0")
ET.SubElement(root, "mxCell", id="1", parent="0")
for c in cells:
    root.append(c)

tree = ET.ElementTree(graph)
ET.indent(tree, space="  ")

out = "garuda_architecture.drawio"
with open(out, "wb") as f:
    f.write(b'<?xml version="1.0" encoding="UTF-8"?>\n')
    tree.write(f, encoding="utf-8", xml_declaration=False)

print(f"Generated: {out}")
print("Open in draw.io → File → Open From → This Device")
