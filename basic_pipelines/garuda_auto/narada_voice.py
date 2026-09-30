"""Narada's voice: ElevenLabs Speech Engine in front of the NIM agent.

ElevenLabs owns the audio: the browser's microphone, speech-to-text,
turn-taking, and speaking the reply. Garuda owns the thinking. For every
finished user turn ElevenLabs opens (or reuses) a WebSocket to
/ws/narada-voice and sends the transcript; the reply text goes back and is
spoken in the browser.

Who is talking: a conversation can only start with a token from
/api/narada/voice/token, which needs a signed-in session. The token response
carries the conversation id, so the id is bound to that user, role and
product (Garuda or Drishti) before any audio flows. A transcript for an id we
never issued is refused. The WebSocket itself is authenticated by the JWT
ElevenLabs signs with our API key.

Blocking SDK calls (token issue) run in a thread; the WebSocket side is async.
"""
import asyncio
import logging
import random
import threading
import time

log = logging.getLogger(__name__)

TTS_MODEL = "eleven_v4_turbo"     # scripts/setup_narada_voice.py configures the engine with it
INFO_TTL_S = 60                  # the info panel must not hammer the ElevenLabs API
BINDING_TTL_S = 15 * 60          # longer than the 5-minute conversation cap
MAX_SPOKEN_CHARS = 400           # free plan: 10k characters a month
UNKNOWN_REPLY = "This conversation was not started from Garuda. Please reopen Narada."
# NIM with tools takes one to five seconds. Past this point a short filler is
# spoken first, the way a person says "hmm" while they think.
FILLER_AFTER_S = 1.1
FILLERS = ("Mm, one sec.", "Sure, let me check.", "Okay, on it.", "Hmm, give me a moment.",
           "Right, one moment.")


def _spoken(text):
    """Trim a reply to something worth saying aloud, at a sentence end."""
    text = " ".join((text or "").split())
    if len(text) <= MAX_SPOKEN_CHARS:
        return text
    cut = text[:MAX_SPOKEN_CHARS]
    end = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
    return cut[:end + 1] if end > 80 else cut.rstrip() + "..."


class NaradaVoice:
    def __init__(self, api_key="", engine_id="", *, reply_fn, on_turn=None,
                 clock=time.time, client=None, voice_id=""):
        self.api_key = api_key or ""
        self.engine_id = engine_id or ""
        self.voice_id = voice_id or ""
        self._info = None                  # (expires, dict)
        self._reply_fn = reply_fn          # (text, user, role, scope) -> dict
        self._on_turn = on_turn            # (user, heard, said) -> None, for the logs
        self._clock = clock
        self._client = client
        self._lock = threading.Lock()
        self._bindings = {}                # conversation_id -> (user, role, scope, expires)

    @property
    def configured(self):
        return bool(self.api_key and self.engine_id)

    def _sdk(self):
        if self._client is None:
            from elevenlabs import ElevenLabs
            self._client = ElevenLabs(api_key=self.api_key)
        return self._client

    # ── info panel ────────────────────────────────────────────────────────────

    def info(self):
        """Model, voice and this month's character use. Blocking; cached."""
        base = {"configured": self.configured, "tts_model": TTS_MODEL}
        if not self.configured:
            return base
        now = self._clock()
        if self._info and self._info[0] > now:
            return {**base, **self._info[1]}
        extra = {}
        try:
            sub = self._sdk().user.subscription.get()
            extra.update(characters_used=sub.character_count, character_limit=sub.character_limit,
                         tier=sub.tier)
        except Exception as exc:
            log.warning("narada voice: usage lookup failed (%s)", type(exc).__name__)
        if self.voice_id:
            try:
                extra["voice"] = self._sdk().voices.get(self.voice_id).name.split(" - ")[0]
            except Exception:
                pass
        self._info = (now + INFO_TTL_S, extra)
        return {**base, **extra}

    # ── tokens ────────────────────────────────────────────────────────────────

    def issue_token(self, user, role, scope):
        """A one-conversation WebRTC token, bound to the caller. Blocking."""
        resp = self._sdk().conversational_ai.conversations.get_webrtc_token(
            agent_id=self.engine_id, participant_name=user or None)
        conversation_id = getattr(resp, "conversation_id", None)
        if not conversation_id:
            raise RuntimeError("ElevenLabs returned a token without a conversation id")
        now = self._clock()
        with self._lock:
            self._bindings = {k: v for k, v in self._bindings.items() if v[3] > now}
            self._bindings[conversation_id] = (user, role, scope, now + BINDING_TTL_S)
        return {"token": resp.token, "conversation_id": conversation_id}

    def binding(self, conversation_id):
        with self._lock:
            b = self._bindings.get(conversation_id or "")
        if b is None or b[3] <= self._clock():
            return None
        return b[:3]

    # ── the WebSocket ElevenLabs connects to ──────────────────────────────────

    def verify(self, headers):
        raw = headers.get("x-elevenlabs-speech-engine-authorization")
        if not raw or not self.api_key:
            return False
        try:
            from elevenlabs.speech_engine.resource import verify_speech_engine_jwt
            verify_speech_engine_jwt(raw, self.api_key)
            return True
        except Exception as exc:
            log.warning("narada voice: rejected connection (%s)", type(exc).__name__)
            return False

    async def reply_for(self, conversation_id, transcript):
        """Reply text for the latest user turn of one conversation."""
        heard = next((m.content for m in reversed(transcript) if m.role == "user"), "").strip()
        if not heard:
            return None
        bound = self.binding(conversation_id)
        if bound is None:
            log.warning("narada voice: transcript for unbound conversation %s", conversation_id)
            return UNKNOWN_REPLY
        user, role, scope = bound
        try:
            result = await asyncio.to_thread(self._reply_fn, heard, user, role, scope)
            said = _spoken(result.get("reply", ""))
        except Exception as exc:
            log.exception("narada voice: reply failed")
            said = f"Something went wrong ({type(exc).__name__}). Nothing was changed."
        if self._on_turn:
            try:
                self._on_turn(user, heard, said)
            except Exception:
                log.exception("narada voice: turn logging failed")
        return said or "Done."

    async def serve(self, websocket):
        """Run one Speech Engine session on an accepted FastAPI WebSocket."""
        from elevenlabs.speech_engine.session import SpeechEngineSession
        session = SpeechEngineSession(websocket)

        async def on_transcript(transcript):
            await session.send_response(self.speak(session.conversation_id, transcript))

        session.on("user_transcript", on_transcript)
        await session.run()

    async def speak(self, conversation_id, transcript):
        """Stream the reply: a filler first if the brain is slow, then the answer."""
        task = asyncio.ensure_future(self.reply_for(conversation_id, transcript))
        try:
            done, _ = await asyncio.wait({task}, timeout=FILLER_AFTER_S)
            if not done:
                yield random.choice(FILLERS) + " "
            said = await task
            if said:
                yield said
        finally:
            if not task.done():
                task.cancel()      # interrupted: a newer turn replaced this one
