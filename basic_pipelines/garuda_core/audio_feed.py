"""The room's sound, live, for the camera view (/ws/audio).

One capture serves every listener. It records the USB microphone through
PipeWire's Pulse server (parec), not the ALSA device itself, so other
programs can share the microphone. The capture runs only while someone is
listening.

Lag is kept out by size, at every step: 20 ms chunks, a 10 ms capture buffer,
and a per-listener queue that keeps only the newest few chunks. A listener
that falls behind loses old sound instead of hearing it late.
"""
import asyncio
import collections
import io
import logging
import math
import os
import shutil
import subprocess
import threading
import time

log = logging.getLogger(__name__)

RATE = 16000                 # speech is clear at 16 kHz, and it is 32 KB/s
CHANNELS = 1
CHUNK_MS = 20
CHUNK_BYTES = RATE * CHANNELS * 2 * CHUNK_MS // 1000      # s16le
LISTENER_QUEUE = 6           # 120 ms; anything older is dropped
IDLE_STOP_S = 3.0            # keep the capture warm across a quick reconnect
METER_KEEP = 30 * 1000 // CHUNK_MS     # 30 s of per-chunk levels
MAX_CLIP_S = 30
# Room loudness by RMS level (dBFS) over the measured span. Set against this
# USB microphone: a still room with the Pi's fan running reads about -49.
QUIET_BELOW_DB = -42.0
LOUD_ABOVE_DB = -20.0
SILENT_DB = -100.0           # the floor reported for digital silence


def sound_word(db):
    if db is None:
        return "unknown"
    return "quiet" if db < QUIET_BELOW_DB else "loud" if db > LOUD_ABOVE_DB else "talking"


def chunk_levels(chunk):
    """(rms, peak) of one s16le chunk, both as fractions of full scale."""
    import array
    a = array.array("h", chunk)
    if not a:
        return 0.0, 0.0
    peak = max(abs(min(a)), abs(max(a))) / 32768
    rms = math.sqrt(sum(x * x for x in a) / len(a)) / 32768
    return rms, peak


def to_db(x):
    return round(20 * math.log10(x), 1) if x > 0 else SILENT_DB


def wav_bytes(pcm):
    import wave
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(CHANNELS)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(pcm)
    return out.getvalue()


def _pulse_env():
    """parec needs the Pulse socket; the systemd service has no XDG_RUNTIME_DIR."""
    env = dict(os.environ)
    if "PULSE_SERVER" not in env:
        runtime = env.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
        sock = os.path.join(runtime, "pulse", "native")
        if os.path.exists(sock):
            env["PULSE_SERVER"] = f"unix:{sock}"
    return env


def find_source(env=None):
    """GARUDA_AUDIO_SOURCE if set, else the first USB input, else the default."""
    chosen = os.environ.get("GARUDA_AUDIO_SOURCE", "").strip()
    if chosen:
        return chosen
    try:
        out = subprocess.run(["pactl", "list", "short", "sources"], capture_output=True,
                             text=True, timeout=3, env=env or _pulse_env()).stdout
    except Exception:
        return None
    names = [line.split("\t")[1] for line in out.splitlines() if "\t" in line]
    inputs = [n for n in names if not n.endswith(".monitor")]
    usb = [n for n in inputs if "usb" in n.lower()]
    return (usb or inputs or [None])[0]


def parec_command(source):
    cmd = ["parec", "--raw", "--format=s16le", f"--rate={RATE}", f"--channels={CHANNELS}",
           "--latency-msec=10", "--process-time-msec=5"]
    if source:
        cmd.append(f"--device={source}")
    return cmd


class AudioFeed:
    def __init__(self, command=None, env=None):
        # command() -> argv; tests pass a fake that writes PCM to stdout.
        self._command = command
        self._env = env
        self._lock = threading.Lock()
        self._listeners = set()          # (loop, asyncio.Queue)
        self._thread = None
        self._proc = None
        self._idle_since = None
        self._keep_until = 0.0
        self._meter = collections.deque(maxlen=METER_KEEP)   # (monotonic, rms, peak)
        self.status = "idle"             # idle | live | unavailable: <why>
        self.source = None

    @property
    def available(self):
        return self._command is not None or shutil.which("parec") is not None

    def describe(self):
        """The first message on /ws/audio: what the chunks that follow are."""
        return {"format": "s16le", "rate": RATE, "channels": CHANNELS, "chunk_ms": CHUNK_MS}

    # ── the meter: for /api/audio/level, Narada and shortcut facts ──────────
    def keep_alive(self, seconds):
        """Keep the capture running `seconds` more without a listener."""
        with self._lock:
            self._keep_until = max(self._keep_until, time.monotonic() + seconds)
            self._ensure_thread()

    def level(self, seconds=1.0):
        """Loudness over the last `seconds`, or None when the capture has not
        covered that span (it was not running)."""
        now = time.monotonic()
        with self._lock:
            recent = [(t, r, p) for t, r, p in self._meter if now - t <= seconds]
        if not recent or now - recent[0][0] < seconds * 0.9 or now - recent[-1][0] > 0.5:
            return None
        rms = math.sqrt(sum(r * r for _, r, _ in recent) / len(recent))
        peak = max(p for _, _, p in recent)
        db = to_db(rms)
        return {"db": db, "peak_db": to_db(peak), "sound": sound_word(db),
                "seconds": round(recent[-1][0] - recent[0][0] + CHUNK_MS / 1000, 1)}

    async def measure(self, seconds=2.0, timeout=None):
        """level(seconds), starting the capture and waiting for it if need be."""
        seconds = max(0.5, min(float(seconds), 10.0))
        got = self.level(seconds)
        if got is not None:
            return got
        self.keep_alive(seconds + 2)
        deadline = time.monotonic() + (timeout or seconds + 3)
        while time.monotonic() < deadline:
            await asyncio.sleep(0.1)
            got = self.level(seconds)
            if got is not None:
                return got
        return None

    async def record(self, seconds):
        """The next `seconds` of sound as PCM, or None if none came."""
        seconds = max(1.0, min(float(seconds), MAX_CLIP_S))
        need = int(seconds * 1000 / CHUNK_MS)
        entry = self.subscribe(queue_size=need + 10)
        parts = []
        try:
            deadline = time.monotonic() + seconds + 3
            while len(parts) < need and time.monotonic() < deadline:
                try:
                    parts.append(await asyncio.wait_for(entry[1].get(), 1.0))
                except asyncio.TimeoutError:
                    pass
        finally:
            self.unsubscribe(entry)
        return b"".join(parts) if parts else None

    # ── listeners (event loop side) ──────────────────────────────────────────
    def subscribe(self, queue_size=LISTENER_QUEUE):
        q = asyncio.Queue(maxsize=queue_size)
        entry = (asyncio.get_running_loop(), q)
        with self._lock:
            self._listeners.add(entry)
            self._idle_since = None
            self._ensure_thread()
        return entry

    def _ensure_thread(self):
        # Called with the lock held.
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(target=self._run, name="audio-feed", daemon=True)
            self._thread.start()

    def unsubscribe(self, entry):
        with self._lock:
            self._listeners.discard(entry)
            if not self._listeners:
                self._idle_since = time.monotonic()

    @staticmethod
    def _offer(q, chunk):
        if q.full():
            try:
                q.get_nowait()           # newest wins: drop the oldest chunk
            except asyncio.QueueEmpty:
                pass
        q.put_nowait(chunk)

    # ── capture (thread side) ────────────────────────────────────────────────
    def _argv(self):
        if self._command is not None:
            return self._command()
        self.source = find_source(self._env or _pulse_env())
        return parec_command(self.source)

    def _should_stop(self):
        with self._lock:
            now = time.monotonic()
            if self._listeners or now < self._keep_until:
                return False
            idle_since = max(self._idle_since or 0.0, self._keep_until)
            return now - idle_since >= IDLE_STOP_S

    def _run(self):
        while True:
            self._capture_while_wanted()
            with self._lock:
                # Someone may have arrived while this thread was winding down;
                # they saw it alive and did not start another.
                if self.available and (self._listeners or time.monotonic() < self._keep_until):
                    continue
                self._thread = None
                if self.available:
                    self.status = "idle"
                return

    def _capture_while_wanted(self):
        backoff = 0.5
        while not self._should_stop():
            if not self.available:
                self.status = "unavailable: parec is not installed"
                return
            started = time.monotonic()
            try:
                self._proc = subprocess.Popen(self._argv(), stdout=subprocess.PIPE,
                                              stderr=subprocess.DEVNULL, bufsize=0,
                                              env=self._env or _pulse_env())
                self.status = "live"
                self._pump(self._proc.stdout)
            except Exception as exc:
                self.status = f"unavailable: {type(exc).__name__}"
                log.warning("audio feed: capture failed: %s", exc)
            finally:
                self._kill()
            if self._should_stop():
                break
            # A capture that ran a while is restarted at once; one that keeps
            # dying (microphone unplugged) is retried more and more slowly.
            backoff = 0.5 if time.monotonic() - started > 5 else min(backoff * 2, 8.0)
            if self.status == "live":
                self.status = "unavailable: the microphone stopped"
            time.sleep(backoff)

    def _pump(self, stdout):
        fd = stdout.fileno()
        buf = bytearray()
        while True:
            data = os.read(fd, CHUNK_BYTES - len(buf))
            if not data:
                return                   # parec exited
            buf += data
            if len(buf) < CHUNK_BYTES:
                continue
            chunk, buf = bytes(buf), bytearray()
            rms, peak = chunk_levels(chunk)
            with self._lock:
                self._meter.append((time.monotonic(), rms, peak))
                listeners = list(self._listeners)
            for loop, q in listeners:
                try:
                    loop.call_soon_threadsafe(self._offer, q, chunk)
                except RuntimeError:     # that listener's loop has closed
                    pass
            if not listeners and self._should_stop():
                return

    def _kill(self):
        proc, self._proc = self._proc, None
        if proc is None:
            return
        try:
            proc.terminate()
            proc.wait(timeout=1)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    def stop(self):
        """Service shutdown: end the capture now."""
        with self._lock:
            self._listeners.clear()
            self._keep_until = 0.0
            self._idle_since = time.monotonic() - IDLE_STOP_S
        self._kill()


FEED = AudioFeed()
