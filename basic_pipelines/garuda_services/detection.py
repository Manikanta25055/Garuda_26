"""Detection: the per-frame GStreamer callback, the night-presence check and camera source resolution.

Moved out of Garuda_web.py (2026-10). Function bodies are unchanged except
that names belonging to Garuda_web are read, and assigned, through `core`: the
live module, handed over once by bind(). Garuda_web imports these functions
back under the same names.
"""
import cv2
import datetime
import numpy as np
import subprocess
import threading
import time

core = None

# Seconds of a uniform picture before the lens counts as covered.
BLIND_ALERT_S = 10.0


def bind(module):
    """Called by Garuda_web with itself, before anything here runs."""
    global core
    if core is not None and core is not module:
        # A second copy of Garuda_web (imported under another name) would
        # silently take over the state every function here reads.
        raise RuntimeError(f"{__name__} is already bound to {core.__name__}; "
                           f"refusing a second copy, {module.__name__}")
    core = module


def _check_night_presence():
    """Activate yellow night-presence alarm if a person is detected in the configured window (IST)."""
    with core.STATE.alerts.night_presence_lock:
        win = dict(core.STATE.config.night_presence_window)   # snapshot — avoids race with config update
    # Armed Away: nobody should be in, so a person at any hour raises it.
    away = getattr(core, "ARM", None) is not None and core.person_alert_wanted(core, core.ARM)
    if not win.get("enabled", True) and not away:
        return
    now_ist = datetime.datetime.now(core._IST) if core._IST is not None else datetime.datetime.now()
    now_hm = now_ist.strftime("%H:%M")
    start, end = win.get("start", "01:30"), win.get("end", "05:00")
    # Handle window that wraps midnight (e.g. 23:00 → 05:00)
    if start <= end:
        in_window = start <= now_hm < end
    else:
        in_window = now_hm >= start or now_hm < end
    if in_window or away:
        with core.STATE.alerts.night_presence_lock:
            core.STATE.alerts.night_presence_active = True
            core.STATE.alerts.night_presence_end_time = time.time() + 10


def app_callback(pad, info, user_data):
    buffer = info.get_buffer()
    if buffer is None:
        return core.Gst.PadProbeReturn.OK

    user_data.increment()
    core.STATE.camera.total_frames += 1
    frame_num = user_data.get_count()
    text_info = f"Frame: {frame_num}\n"
    format_, width, height = core.get_caps_from_pad(pad)

    # The frame is copied out of the buffer only when something here uses it:
    # the published picture (15 a second), the light reading for the rules and
    # a danger alert's second look. Copying every frame of the pipeline, to use
    # a fraction of them, was the largest fixed cost of this callback.
    can_read = bool(user_data.use_frame and format_ and width and height)
    _frame_box = []

    def _frame():
        if not _frame_box:
            _frame_box.append(core.get_numpy_from_buffer(buffer, format_, width, height)
                              if can_read else None)
        return _frame_box[0]

    publish = can_read and core._frame_publisher.due()

    # Camera blindness detection — flag if camera is covered/blocked. Checked
    # on the published frames and timed in seconds: it counted frames, and
    # "300 frames, about 10 s at 30 fps" was 5 s at the pipeline's 60.
    if publish and _frame() is not None:
        frame = _frame()
        # Every 4th pixel each way: a covered lens is uniform at any scale.
        gray = cv2.cvtColor(np.ascontiguousarray(frame[::4, ::4]), cv2.COLOR_RGB2GRAY)
        variance = float(np.var(gray))
        if variance < 50:   # nearly uniform → blocked/covered
            now_b = time.time()
            if not core.STATE.alerts.blind_since:
                core.STATE.alerts.blind_since = now_b
            if now_b - core.STATE.alerts.blind_since >= BLIND_ALERT_S and not core.STATE.alerts.blind_alert_sent:
                core.STATE.alerts.blind_alert_sent = True
                core.log_system_update("[TAMPER] Camera blindness detected — lens may be covered!")
                core._append_detection_perm("TAMPER", "camera_blind", 0.0, "camera appears blocked")
                core.push_urgent_ws()
                # Max-priority: bypass DND/idle and send alert email immediately
                threading.Thread(target=core._send_tamper_email, daemon=True).start()
        else:
            core.STATE.alerts.blind_since = 0.0
            core.STATE.alerts.blind_alert_sent = False

    roi = core.hailo.get_roi_from_buffer(buffer)
    detections = roi.get_objects_typed(core.hailo.HAILO_DETECTION)

    with core.STATE.modes.lock:
        threshold = core.STATE.config.detection_threshold
        privacy = core.STATE.modes.privacy

    # Build case-insensitive lookup sets so UI casing mismatches never break detection
    _danger_set = {lbl.lower() for lbl in user_data.danger_labels}
    _watch_set  = {lbl.lower() for lbl in core.STATE.config.watch_labels}

    # alert, log or ignore for each detection: the per-object rules and zones
    # (garuda_routes/zones.py). Nothing saved means the danger labels alert.
    _rules = getattr(core, "RULES", None)
    if detections and _rules is not None and (_rules.data["rules"] or _rules.data["zones"]):
        _decide = core.FrameRules(core, _rules, getattr(core, "ARM", None), _danger_set)
    else:
        _decide = lambda lbl, box=None: "alert" if lbl.lower() in _danger_set else "log"

    danger_detected = False
    _alert_dets = []
    for d in detections:
        label = d.get_label()
        confidence = d.get_confidence()
        if confidence >= threshold:
            _b = d.get_bbox()
            _act = _decide(label, (_b.xmin(), _b.ymin(), _b.xmax(), _b.ymax()))
            text_info += f"{label} ({confidence:.2f})\n"
            # Privacy blur: blur any detected person (case-insensitive)
            if privacy and publish and label.lower() == "person" and _frame() is not None:
                frame = _frame()
                bbox = d.get_bbox()
                x1 = int(bbox.xmin() * width)
                y1 = int(bbox.ymin() * height)
                x2 = int(bbox.xmax() * width)
                y2 = int(bbox.ymax() * height)
                x1, y1 = max(0, x1), max(0, y1)
                x2, y2 = min(width, x2), min(height, y2)
                if x2 > x1 and y2 > y1:
                    roi_face = frame[y1:y2, x1:x2]
                    roi_face = cv2.GaussianBlur(roi_face, (51, 51), 30)
                    frame[y1:y2, x1:x2] = roi_face
            if _act == "alert":
                _alert_dets.append(d)
                core.STATE.camera.label_consec_frames[label] = core.STATE.camera.label_consec_frames.get(label, 0) + 1
                if core.STATE.camera.label_consec_frames[label] >= 2:   # require 2 consecutive frames to fire
                    danger_detected = True
                core.STATE.alerts.last_danger_conf = confidence
            elif _act == "log" and (label.lower() == "person" or label.lower() in _watch_set
                                    or label.lower() in _danger_set):
                # WATCH: log silently with 30s cooldown to avoid per-frame spam
                now_t = time.time()
                if now_t - core.STATE.camera.watch_last_logged.get(label, 0) >= 30:
                    core.STATE.camera.watch_last_logged[label] = now_t
                    core.log_system_update(f"[WATCH] {label} ({confidence:.2f})")
                    core._append_detection_perm("WATCH", label, confidence)

    # Reset consecutive counts for labels not seen (or below threshold) this frame
    seen_above_thr = {d.get_label() for d in detections if d.get_confidence() >= threshold}
    for k in list(core.STATE.camera.label_consec_frames):
        if k not in seen_above_thr:
            core.STATE.camera.label_consec_frames[k] = 0

    # Find which danger labels were actually detected this frame (for logging)
    _triggered_labels = [d.get_label() for d in _alert_dets]
    if danger_detected:
        core.STATE.alerts.danger_trigger_info = text_info   # snapshot the frame that triggered
        _captured_conf = core.STATE.alerts.last_danger_conf
        _captured_label = _triggered_labels[0] if _triggered_labels else "danger"
        _is_rising_edge = not core.STATE.alerts.danger_active
        if _is_rising_edge:
            core.STATE.alerts.danger_active = True
        # The frame and box of the detection, for the second look before the
        # email (garuda_auto/vision_check.py). Copied only when it is needed.
        _box = next(((b.xmin(), b.ymin(), b.xmax(), b.ymax()) for d in _alert_dets
                     for b in [d.get_bbox()]), None)
        _held_since = core.STATE.alerts.email_held_at
        _look_again = bool(_held_since) and time.time() - _held_since >= core._svc_alerts.RECHECK_S
        _seen = _frame() if (_is_rising_edge or _look_again) else None
        _frame_copy = _seen.copy() if _seen is not None else None

        # Batch all danger work into ONE daemon thread per frame (not 4-5 separate ones)
        def _danger_work(lbl=_captured_label, conf=_captured_conf, rising=_is_rising_edge,
                         seen=_frame_copy, box=_box):
            core.trigger_software_alert()
            if rising:
                # The alarm on the screen is up already; the email waits for
                # the second look (a few seconds at most, and never lost to it).
                look = core.email_after_second_look(seen, box, lbl)
                _danger_key = "__danger__"
                _now = time.time()
                if _now - core.STATE.camera.watch_last_logged.get(_danger_key, 0) >= 60:
                    core.STATE.camera.watch_last_logged[_danger_key] = _now
                    core.log_scissors_detection(lbl)
                    core._append_detection_perm("DANGER", lbl, conf,
                                                "alert triggered; " + core._vision_say(look or {}))
        with core.STATE.alerts.lock:
            _already_alerting = core.STATE.alerts.active
        if _look_again:
            # The email was held as harmless, and the detector has kept seeing
            # it: look again, and send it if the second look now disagrees.
            core.STATE.alerts.email_held_at = time.time()
            threading.Thread(target=core.email_after_second_look,
                             args=(_frame_copy, _box, _captured_label), kwargs={"again": True},
                             daemon=True).start()
        if _already_alerting and not _is_rising_edge:
            # The alert is up: keeping it up is two lock grabs, done here. A
            # new thread for each of the 60 frames a second a knife stays in
            # view was the single largest cost of an alert.
            core.trigger_software_alert()
        else:
            threading.Thread(target=_danger_work, daemon=True).start()
    elif not _triggered_labels:
        # Only reset when NO danger labels are seen at all this frame.
        # Avoids false reset during the 2-frame ramp-up period.
        core.STATE.alerts.danger_active = False
        core.STATE.alerts.email_held_at = 0.0

    # ── Drishti: one descriptor per observation ──────────────────────────────
    # observe() is pure arithmetic. Every piece of I/O a rule causes happens on
    # the rule thread instead, so a slow relay or a dead broker can never back
    # up into the video path. Throttled because the rule loop ticks at 2 Hz and
    # nothing is gained by rebuilding the descriptor thirty times a second.
    _now = time.time()
    if _now - core.STATE.camera.drishti_last_observe >= core._DRISHTI_OBSERVE_INTERVAL_S:
        core.STATE.camera.drishti_last_observe = _now
        try:
            _dets = []
            for d in detections:
                if d.get_confidence() < threshold:
                    continue
                b = d.get_bbox()
                _dets.append({"label": d.get_label().lower(),
                              "bbox": (b.xmin(), b.ymin(), b.xmax(), b.ymax())})
            # Subsampled: a full mean on every frame is not worth the cycles,
            # and ambient light does not change between adjacent pixels.
            _lframe = _frame()
            _luma = float(_lframe[::8, ::8].mean()) if _lframe is not None else 0.0
            core.DRISHTI_RUNTIME.observe(_dets, _luma)
        except Exception as exc:
            core.log_system_update(f"[DRISHTI] observe failed: {exc}")

    user_data.person_detected = any(d.get_label().lower() == "person" for d in detections)
    if user_data.person_detected:
        # Cheap and thread-safe, so it runs here; once a second is plenty for
        # a banner that stays up ten seconds (it used to start a thread on
        # every frame with a person in it).
        if _now - core.STATE.alerts.night_presence_last_check >= 1.0:
            core.STATE.alerts.night_presence_last_check = _now
            try:
                core._check_night_presence()
            except Exception as exc:
                core.log_system_update(f"[NIGHT] presence check failed: {exc}")

    # Gated: the encode is the most expensive thing in this callback and no
    # browser can use 60fps MJPEG. Nothing is drawn on the frame -- the debug
    # readout that used to be here also reached the WebRTC track and every saved
    # evidence clip, because all three read _frame_raw.
    if publish and _frame() is not None:
        frame_bgr, jpeg = core.FramePublisher.encode(_frame())
        with core.STATE.camera.frame_lock:
            core.STATE.camera.frame_buffer = jpeg
            core.STATE.camera.frame_raw    = frame_bgr
            core.STATE.camera.frame_seq += 1
            core.STATE.camera.frame_ts     = time.time()
        user_data.set_frame(frame_bgr)

        # Clip recording — write current frame if active
        _clip_autostopped = False
        _clip_autostopped_path = ""
        with core.STATE.camera.clip_lock:
            if core.STATE.camera.clip_writer is not None:
                try:
                    core.STATE.camera.clip_writer.write(frame_bgr)
                except Exception:
                    pass
                if time.time() - core.STATE.camera.clip_start_time > 60:
                    core.STATE.camera.clip_writer.release()
                    core.STATE.camera.clip_writer = None
                    _clip_autostopped = True
                    _clip_autostopped_path = core.STATE.camera.clip_path
        if _clip_autostopped:
            core.log_system_update("Clip auto-stopped after 60 s.")
            core.push_urgent_ws()   # notify JS so it can reset the record button
            if _clip_autostopped_path:
                threading.Thread(target=core.exfiltrate_clip, args=(_clip_autostopped_path,),
                                 daemon=True).start()

    core.STATE.camera.latest_detection_info = text_info
    return core.Gst.PadProbeReturn.OK


def _resolve_camera(input_src: str) -> str:
    """
    Resolve the camera source for GStreamer.
    - If input is not a /dev/videoN device, return as-is (file or 'rpi').
    - If it IS a /dev/videoN, check via v4l2-ctl whether it is a Pi-internal
      device (rp1-cfe / pispbe). If so, find a real USB camera or fall back
      to 'rpi' (libcamera via GStreamer, which works here unlike OpenCV).
    """
    if not input_src.startswith("/dev/video"):
        return input_src

    try:
        result = subprocess.run(
            ["v4l2-ctl", "--list-devices"],
            capture_output=True, text=True, timeout=3
        )
        # Build map: device_path → category_name
        dev_category: dict = {}
        current = ""
        for line in result.stdout.splitlines():
            stripped = line.strip()
            if stripped and not line.startswith("\t"):
                current = stripped
            elif stripped.startswith("/dev/video"):
                dev_category[stripped] = current

        def _is_pi_internal(dev: str) -> bool:
            return "platform:" in dev_category.get(dev, "")

        if _is_pi_internal(input_src):
            # Look for a real USB camera (no "platform:" in name)
            for dev, cat in dev_category.items():
                if "platform:" not in cat:
                    core.log_system_update(f"[CAMERA] {input_src} is Pi-internal → using USB: {dev}")
                    return dev
            # No USB camera found; libcamera (GStreamer) works for Pi camera
            core.log_system_update("[CAMERA] No USB camera found → using Pi camera (rpi/libcamera)")
            return "rpi"
    except Exception as e:
        core.log_system_update(f"[CAMERA] v4l2-ctl probe failed: {e}")

    return input_src  # unable to determine — use as-is
