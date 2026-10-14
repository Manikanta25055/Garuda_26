#!/usr/bin/env python3
"""Make Drishti's interface sounds with ElevenLabs sound effects.

    python3 scripts/make_sounds.py --key-file starter_key.txt          # all sounds
    python3 scripts/make_sounds.py --key-file starter_key.txt off lock  # only these
    python3 scripts/make_sounds.py --pick lock=5                        # take 5, no API call

Apple's rule is restraint: most taps are silent, and a sound marks only a
moment that matters (a lock, a photo, a message sent, a task done). Small
moves (tabs, filters, sheets) get a haptic tick and no sound; see
garuda_web/sound.js for which control plays what.

Every sound means one thing and the same thing everywhere, and pitch carries
direction: rising = on / start / sent, falling = off / stop. Clicks are a
few tens of milliseconds, outcomes under a second; only the security alarm
is allowed to be loud.

ElevenLabs cannot make anything shorter than half a second, so each take is
cut here: the silence before the sound is dropped, it is cut to its length,
faded out, and peak-normalised. Several takes are asked for and the one whose
sound fits its window best is kept; raw takes stay in the cache, so a
different take can be picked later without paying again.
"""
import argparse
import json
import os
import subprocess
import sys
import urllib.request
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "basic_pipelines/garuda_web/sounds"
CACHE = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "drishti_sounds"
API = "https://api.elevenlabs.io/v1/sound-generation"
RATE = 44100
DRY = "high quality studio recording, clean, dry, no reverb, no background noise, no music, no voice"

# name: (prompt, keep ms, fade ms, tonal)
SOUNDS = {
    # state changes
    "off": ("A small premium toggle switch snapping off, a soft click slightly lower and duller", 130, 60, False),
    "lock": ("One single crisp soft click of a small premium latch closing, one click only, a smartphone screen locking", 220, 110, False),
    "unlock": ("A soft light mechanical unlatch click with a tiny airy lift, a premium device unlocking, gentle", 320, 160, False),
    # The first delete was heard as "[glass breaking]": in a security app that
    # startles. Paper, slowly, and nothing sharp.
    "delete": ("A soft slow crumple of a single sheet of thin paper in a hand, gentle, muffled and quiet, "
               "nothing sharp, nothing breaking", 420, 200, False),
    "shutter": ("A crisp quiet camera shutter click, a premium smartphone camera taking a photo", 300, 120, False),
    # messages
    "send": ("A quick soft whoosh that rises and fades away, a message being sent, airy and light", 380, 220, False),
    "receive": ("A gentle soft two-note glass ping, a message arriving, warm and quiet notification", 520, 300, True),
    # outcomes
    "success": ("A gentle bright two-note ascending marimba chime, a task completed, soft warm and pleasant", 650, 350, True),
    "warning": ("A single soft low glass bell tone, a calm attention cue, gentle and not alarming", 550, 320, True),
    "error": ("Two soft low muted thuds descending in pitch, a gentle error cue, rounded and calm", 480, 220, False),
    # recording and voice
    "rec-start": ("A short soft rising electronic tone, video recording started, gentle premium interface sound", 320, 160, True),
    "rec-stop": ("A short soft falling electronic tone, video recording stopped, gentle premium interface sound", 320, 160, True),
    # "Shimmering" made a choir: the first voice-start was heard as a woman
    # saying "Oh well". Now plainly instrumental, and every take is checked.
    "voice-start": ("Two soft clean electronic sine tones gliding upward in pitch, a glass bell chime, "
                    "a voice assistant starts listening, purely instrumental, no human voice, no vocals, "
                    "no choir, no humming", 620, 320, True),
    "voice-end": ("A soft shimmering chime that slides downward in pitch, a voice assistant stops listening, airy and warm", 620, 320, True),
    # the one loud sound
    "alert": ("An urgent clean two-tone electronic security alert chime, a home security alarm, clear and attention-grabbing", 1300, 250, True),
}

# Takes chosen by measuring them (the score is only a first guess):
# pitch tracked every 40 ms for direction, bursts counted for "one click".
PICKS = {
    "off": 1,          # one 32 ms click, the warmest of four
    "lock": 4,         # click and body, 76 ms (asked twice: the first four were several clicks)
    "unlock": 1,       # latch then lift, 150 ms
    "delete": 4,       # heard as "[paper rustling]" (take 2 was "[glass breaking]")
    "shutter": 2,      # one shutter, warmer than the brighter takes
    "send": 0,         # the whoosh that rises
    "receive": 1,      # one soft G5 ping
    "success": 1,      # two notes rising a minor third (444 -> 530 Hz)
    "warning": 3,      # one low bell, steady
    "error": 1,        # two low thuds
    "rec-start": 1,    # glides up 660 -> 1900 Hz
    "rec-stop": 2,     # glides down 750 -> 460 Hz
    "voice-start": 7,  # a clean glide up 385 -> 668 Hz (take 1, a shimmer, had a voice in it)
    "voice-end": 4,    # shimmer falling 1000 -> 710 Hz, its mirror
    "alert": 1,        # two tones, a fifth apart, repeating
}

# Made from another sound instead of asked for: (source, semitones). The
# switch's on and off are one click at two pitches, so they read as a pair.
DERIVED = {"on": ("off", 2.0)}


def has_words(key, path):
    """True when speech-to-text hears words in a take: a sound must not talk.

    Scribe tags non-speech as "[chime]", "[swish]"; anything else is a voice.
    """
    import requests
    with open(path, "rb") as f:
        r = requests.post("https://api.elevenlabs.io/v1/speech-to-text", headers={"xi-api-key": key},
                          data={"model_id": "scribe_v1", "tag_audio_events": "true"},
                          files={"file": (path.name, f, "audio/mpeg")}, timeout=60)
    r.raise_for_status()
    import re
    return bool(re.sub(r"[\[(][^\])]*[\])]", "", r.json().get("text", "")).strip(" .,!?"))


def api_key(args):
    if args.key_file:
        raw = Path(args.key_file).read_text().strip()
        return raw.split("=", 1)[1].strip() if "=" in raw else raw
    key = os.environ.get("ELEVENLABS_API_KEY", "")
    if not key:
        sys.exit("No key: pass --key-file or set ELEVENLABS_API_KEY")
    return key


def generate(key, prompt, seconds, influence):
    body = json.dumps({
        "text": f"{prompt}. {DRY}.",
        "model_id": "eleven_text_to_sound_v2",
        "duration_seconds": seconds,
        "prompt_influence": influence,
    }).encode()
    req = urllib.request.Request(f"{API}?output_format=mp3_44100_128", data=body, method="POST",
                                 headers={"xi-api-key": key, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()


def decode(path):
    pcm = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-ac", "1", "-ar", str(RATE), "-f", "f32le", "-"],
                         check=True, capture_output=True).stdout
    return np.frombuffer(pcm, dtype=np.float32).astype(np.float64)


def shape(x, keep_ms, fade_ms):
    """Drop the lead-in, cut to length, fade out, normalise. Returns (clip, fit)."""
    x = x - x.mean()
    peak = np.abs(x).max() or 1.0
    onset = int(np.argmax(np.abs(x) > peak * 0.06))
    onset = max(0, onset - int(RATE * 0.002))
    body = x[onset:]
    n = min(len(body), int(RATE * keep_ms / 1000))
    clip = body[:n].copy()
    fade = min(n, int(RATE * fade_ms / 1000))
    clip[n - fade:] *= np.cos(np.linspace(0, np.pi / 2, fade)) ** 2
    clip[:int(RATE * 0.0015)] *= np.linspace(0, 1, int(RATE * 0.0015))
    clip *= 10 ** (-3 / 20) / (np.abs(clip).max() or 1.0)
    # How much of the sound lives inside its window: a take that keeps going
    # after the cut would sound chopped off.
    fit = float((body[:n] ** 2).sum() / max((body ** 2).sum(), 1e-12))
    return clip, fit


def flatness(x):
    spec = np.abs(np.fft.rfft(x * np.hanning(len(x)))) + 1e-12
    return float(np.exp(np.log(spec).mean()) / spec.mean())


def score(clip, fit, tonal):
    s = fit
    if tonal:
        s *= 1 - 0.6 * flatness(clip)          # chimes should ring, not hiss
    attack = int(np.argmax(np.abs(clip))) / RATE
    s *= 1 / (1 + 20 * attack)                 # the hit should land at once
    return s


def repitch(clip, semitones):
    """Higher and shorter (or lower and longer), the way a sampler plays it."""
    ratio = 2 ** (semitones / 12)
    pos = np.arange(0, len(clip) - 1, ratio)
    return np.interp(pos, np.arange(len(clip)), clip)


def encode(clip, path):
    pcm = (np.clip(clip, -1, 1) * 32767).astype("<i2").tobytes()
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "s16le", "-ar", str(RATE), "-ac", "1", "-i", "-",
                    "-c:a", "libmp3lame", "-b:a", "128k", str(path)], input=pcm, check=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("names", nargs="*")
    ap.add_argument("--key-file")
    ap.add_argument("--takes", type=int, default=4)
    ap.add_argument("--influence", type=float, default=0.6)
    ap.add_argument("--pick", action="append", default=[], help="name=take: use that take instead of the best")
    ap.add_argument("--fresh", action="store_true", help="ask for new takes even if some are cached")
    args = ap.parse_args()

    picks = {**PICKS, **dict(p.split("=") for p in args.pick)}
    names = args.names or (list(dict(p.split("=") for p in args.pick)) if args.pick else list(SOUNDS))
    unknown = [n for n in names if n not in SOUNDS]
    if unknown:
        sys.exit(f"unknown sounds: {unknown}")
    OUT.mkdir(parents=True, exist_ok=True)
    CACHE.mkdir(parents=True, exist_ok=True)
    key = None
    chosen_clip = {}

    for name in names:
        prompt, keep, fade, tonal = SOUNDS[name]
        takes = sorted(CACHE.glob(f"{name}.*.mp3"))
        if args.fresh or (not takes and name not in picks):
            key = key or api_key(args)
            first = len(takes)
            for i in range(first, first + args.takes):
                seconds = max(0.5, round(keep / 1000 + 0.3, 1))
                (CACHE / f"{name}.{i}.mp3").write_bytes(generate(key, prompt, seconds, args.influence))
            takes = sorted(CACHE.glob(f"{name}.*.mp3"))
        rated = []
        if key or os.environ.get("ELEVENLABS_API_KEY") or args.key_file:
            key = key or api_key(args)
            spoken = [t for t in takes if has_words(key, t)]
            for t in spoken:
                print(f"{name:12s} take {t.stem.split('.')[-1]} has a voice in it: left out")
            takes = [t for t in takes if t not in spoken]
            if not takes:
                sys.exit(f"{name}: every take has a voice in it; ask for more with --fresh")
        for t in takes:
            clip, fit = shape(decode(t), keep, fade)
            rated.append((score(clip, fit, tonal), int(t.stem.split(".")[-1]), clip))
        chosen = next((r for r in rated if name in picks and r[1] == int(picks[name])), None) \
            or max(rated, key=lambda r: r[0])
        encode(chosen[2], OUT / f"{name}.mp3")
        chosen_clip[name] = chosen[2]
        print(f"{name:12s} take {chosen[1]} of {len(rated)}  "
              + "  ".join(f"#{i}:{s:.2f}" for s, i, _ in sorted(rated, key=lambda r: r[1])))

    for name, (source, semitones) in DERIVED.items():
        if source in chosen_clip:
            encode(repitch(chosen_clip[source], semitones), OUT / f"{name}.mp3")
            print(f"{name:12s} = {source} {semitones:+g} semitones")


if __name__ == "__main__":
    main()
