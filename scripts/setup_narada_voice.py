#!/usr/bin/env python3
"""Create (or re-point) Narada's ElevenLabs Speech Engine and record its id in .env.

    python3 scripts/setup_narada_voice.py
    python3 scripts/setup_narada_voice.py --host drishti.veeramanikanta.in --voice cjVigY5qzO86Huf0OWal

ElevenLabs handles the browser microphone, speech-to-text, turn-taking and
the spoken reply; every transcript is sent to Garuda's /ws/narada-voice, where
the NIM agent decides what to do. The engine therefore only needs to know
that WebSocket URL, a voice and the turn-taking limits.

Reads ELEVENLABS_API_KEY from .env and never prints it. Running it again
updates the engine already in .env instead of creating a second one.
Restart garuda-web afterwards.
"""
import argparse
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "basic_pipelines"))
from garuda_auto.envfile import set_vars  # noqa: E402
from garuda_auto.narada_voice import TTS_MODEL  # noqa: E402

ENV_PATH = ROOT / ".env"
DEFAULT_HOST = "garuda.veeramanikanta.in"
# Eric: smooth, conversational. (George, the first pick, reads like a narrator.)
DEFAULT_VOICE = "cjVigY5qzO86Huf0OWal"
KEYWORDS = ["Narada", "Garuda", "Drishti", "DND", "privacy mode", "night mode"]


def read_env():
    out = {}
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                name, value = line.split("=", 1)
                out[name.strip()] = value.strip().strip("'\"")
    return out


def engine_config(ws_url, voice):
    return dict(
        name="Narada",
        speech_engine={"ws_url": ws_url},
        # eleven_v4_turbo (2026): the newest model, ~100 ms to first audio,
        # with expressive delivery. A little below default stability lets the
        # voice move like speech rather than narration.
        tts={"model_id": TTS_MODEL, "voice_id": voice, "expressive_mode": True,
             "stability": 0.4, "similarity_boost": 0.8, "speed": 1.03},
        asr={"provider": "scribe_realtime", "keywords": KEYWORDS},
        # Hands-free until ~30 s of silence; the free plan is 10k characters
        # a month, so conversations are capped at five minutes.
        turn={"turn_model": "turn_v3", "turn_eagerness": "normal",
              "speculative_turn": True, "silence_end_call_timeout": 30},
        conversation={"max_duration_seconds": 300},
        call_limits={"agent_concurrency_limit": 2},
        overrides={"first_message": True},
        privacy={"record_voice": False},
        language="en",
    )


async def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--host", default=DEFAULT_HOST,
                    help="public host the Cloudflare tunnel serves Garuda on")
    ap.add_argument("--voice", default=None, help="ElevenLabs voice id")
    args = ap.parse_args()

    env = read_env()
    key = env.get("ELEVENLABS_API_KEY", "")
    if not key:
        sys.exit(f"ELEVENLABS_API_KEY is not set in {ENV_PATH}")
    voice = args.voice or env.get("ELEVENLABS_VOICE_ID") or DEFAULT_VOICE
    ws_url = f"wss://{args.host}/ws/narada-voice"

    from elevenlabs import AsyncElevenLabs
    client = AsyncElevenLabs(api_key=key)
    config = engine_config(ws_url, voice)
    engine_id = env.get("ELEVENLABS_SPEECH_ENGINE_ID", "")
    if engine_id:
        config.pop("overrides")  # fixed at create time; update() does not take it
        await client.speech_engine.update(engine_id, **config)
        print(f"Updated Speech Engine {engine_id} -> {ws_url}")
    else:
        engine = await client.speech_engine.create(**config)
        engine_id = engine.engine_id
        print(f"Created Speech Engine {engine_id} -> {ws_url}")
    set_vars(ENV_PATH, {"ELEVENLABS_SPEECH_ENGINE_ID": engine_id,
                        "ELEVENLABS_VOICE_ID": voice})
    print("Restart garuda-web for the running service to pick it up.")


if __name__ == "__main__":
    asyncio.run(main())
