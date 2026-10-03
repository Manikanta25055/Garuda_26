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
# Indian English needs a paid ElevenLabs plan: the built-in voices are all
# American or British, and on the free plan the API refuses voice-library
# voices (402 paid_plan_required) and voice design (403), both tried
# 2026-10-01. On Starter or above:
#     python3 scripts/setup_narada_voice.py --voice pzxut4zZz4GImZNlqQ3H
# (Raju, "Natural Conversationalist"; also Kaanha 3ilI2SPo3zMSLH6nREK8 and
# Sid 0muxiGNHAVvmM1qWRtyV, same owner). The voice is added to the account.
LIBRARY_VOICE_OWNER = "7398804d9eaf2f463899a907587c33a390591775784f87857b6d0e1e4e3e66f6"
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
        # with expressive delivery. Stability well below default lets the
        # pitch and pace move the way speech does (lower than ~0.3 starts to
        # wobble); speed 1.0 is the voice's own talking pace.
        tts={"model_id": TTS_MODEL, "voice_id": voice, "expressive_mode": True,
             "stability": 0.32, "similarity_boost": 0.8, "speed": 1.0},
        asr={"provider": "scribe_realtime", "keywords": KEYWORDS},
        # Hands-free until ~30 s of silence; the free plan is 10k characters
        # a month, so conversations are capped at five minutes.
        # Eager: answer as soon as the sentence sounds finished instead of
        # waiting out a pause. Speculative: start on the likely-final words.
        turn={"turn_model": "turn_v3", "turn_eagerness": "eager",
              "speculative_turn": True, "silence_end_call_timeout": 30},
        conversation={"max_duration_seconds": 300},
        call_limits={"agent_concurrency_limit": 2},
        overrides={"first_message": True},
        privacy={"record_voice": False},
        language="en",
    )


async def ensure_voice(client, voice, owner):
    """Library voices must be in the account before an engine can use them."""
    try:
        await client.voices.get(voice)
        return voice
    except Exception:
        added = await client.voices.share(public_user_id=owner, voice_id=voice,
                                          new_name="Narada voice")
        print("Added the voice to the account from the voice library.")
        return getattr(added, "voice_id", None) or voice


async def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--host", default=DEFAULT_HOST,
                    help="public host the Cloudflare tunnel serves Garuda on")
    ap.add_argument("--voice", default=None, help="ElevenLabs voice id")
    ap.add_argument("--owner", default=LIBRARY_VOICE_OWNER,
                    help="public owner id, for a voice-library voice not yet in the account")
    args = ap.parse_args()

    env = read_env()
    key = env.get("ELEVENLABS_API_KEY", "")
    if not key:
        sys.exit(f"ELEVENLABS_API_KEY is not set in {ENV_PATH}")
    voice = args.voice or env.get("ELEVENLABS_VOICE_ID") or DEFAULT_VOICE
    ws_url = f"wss://{args.host}/ws/narada-voice"

    from elevenlabs import AsyncElevenLabs
    client = AsyncElevenLabs(api_key=key)
    voice = await ensure_voice(client, voice, args.owner)
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
