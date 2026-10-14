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
import inspect
import logging
import random
import re
import threading
import time

log = logging.getLogger(__name__)

TTS_MODEL = "eleven_v4_turbo"     # scripts/setup_narada_voice.py configures the engine with it
INFO_TTL_S = 60                  # the info panel must not hammer the ElevenLabs API
BINDING_TTL_S = 15 * 60          # longer than the 10-minute conversation cap
UNKNOWN_REPLY = "This conversation was not started from Garuda. Please reopen Narada."
# NIM usually answers in one to two seconds. Only past this point is a short
# filler spoken first, the way a person says "hmm" while they think; any
# sooner and the filler itself delays (and bills for) most answers.
FILLER_AFTER_S = 2.2
PLANNER_CHECK_S = 2.0
PLANNER_LINE = "This one takes some building. Give me a moment."
FILLERS = ("Mm, one sec.", "Sure, let me check.", "Okay, on it.", "Hmm, give me a moment.",
           "Right, one moment.")


def _spoken(text):
    """A reply as one line to say, whole: the owner wants no length cap."""
    return " ".join((text or "").split())


def _sentences(text):
    """The reply in sentence-sized pieces, each with its trailing space.

    Streamed one by one, speech starts on the first sentence instead of
    after the whole answer, and a long answer is never one huge message.
    A list's "3." or a time's "04.11" stays with its sentence.
    """
    return [p for p in re.findall(r".+?(?:(?<!\d)[.!?]+[\"')\]]*(?:\s+|$)|$)", text) if p.strip()]


class NaradaVoice:
    def __init__(self, api_key="", engine_id="", *, reply_fn, on_turn=None,
                 clock=time.time, client=None, voice_id=""):
        self.api_key = api_key or ""
        self.engine_id = engine_id or ""
        self.voice_id = voice_id or ""
        self._info = None                  # (expires, dict)
        self._reply_fn = reply_fn
        # Whether the brain can hand its answer over as it writes it.
        try:
            params = inspect.signature(reply_fn).parameters.values()
            self._streams = any(p.name == "on_text" or p.kind is p.VAR_KEYWORD for p in params)
        except (TypeError, ValueError):
            self._streams = False          # (text, user, role, scope) -> dict
        self._on_turn = on_turn            # (user, heard, said) -> None, for the logs
        self._clock = clock
        self._client = client
        self._lock = threading.Lock()
        self._bindings = {}                # conversation_id -> (user, role, scope, expires)
        self._turns = {}                   # bounded display results, separate from speech
        # (user) -> what Narada is doing for them right now, or None (agent.live_for).
        self.live_fn = None

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
            self._turns = {k: v for k, v in self._turns.items() if k in self._bindings}
            self._bindings[conversation_id] = (user, role, scope, now + BINDING_TTL_S)
            self._turns[conversation_id] = []
        return {"token": resp.token, "conversation_id": conversation_id}

    def turns_for(self, conversation_id, user, role, scope, after=0):
        """Only the bound caller can read a conversation's original display replies."""
        with self._lock:
            bound = self._bindings.get(conversation_id)
            if not bound or bound[3] <= self._clock() or bound[:3] != (user, role, scope):
                return None
            return [t for t in self._turns.get(conversation_id, []) if t["id"] > after]

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

    def _planning(self, conversation_id):
        """Has this conversation's turn gone to the planner?"""
        bound = self.binding(conversation_id)
        if bound is None or self.live_fn is None:
            return False
        try:
            live = self.live_fn(bound[0])
        except Exception:
            return False
        return bool(live) and live.get("lane") == "planner"

    async def reply_for(self, conversation_id, transcript, on_text=None, meta=None):
        """Reply text for the latest user turn of one conversation.

        With `on_text`, the brain hands over the answer a sentence at a time
        as it is written; meta["streamed"] is then whether the final answer
        already went that way (so it is not said twice).
        """
        heard = next((m.content for m in reversed(transcript) if m.role == "user"), "").strip()
        if not heard:
            return None
        bound = self.binding(conversation_id)
        if bound is None:
            log.warning("narada voice: transcript for unbound conversation %s", conversation_id)
            return UNKNOWN_REPLY
        user, role, scope = bound
        try:
            if on_text and self._streams:
                result = await asyncio.to_thread(self._reply_fn, heard, user, role, scope,
                                                 on_text=on_text)
            else:
                result = await asyncio.to_thread(self._reply_fn, heard, user, role, scope)
            said = _spoken(result.get("reply", ""))
            if meta is not None:
                meta["streamed"] = bool(result.get("streamed"))
        except Exception as exc:
            log.exception("narada voice: reply failed")
            said = "Something went wrong. Please check the current state before trying again."
            result = {"reply": said}
        # Keep the checked Markdown and cards before the speech service flattens
        # them. The browser reads this even while audio is still playing.
        with self._lock:
            turns = self._turns.setdefault(conversation_id, [])
            turns.append({"id": turns[-1]["id"] + 1 if turns else 1, "heard": heard,
                          "response": result.get("display_reply") or result.get("reply") or "Done.",
                          **{k: result[k] for k in ("lane", "actions", "proposal", "memory", "offer",
                                                   "observation", "confirm", "artifacts", "steps",
                                                   "images", "planner", "route", "model") if k in result}})
            del turns[:-32]
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

        async def on_error(err, *_):
            # Reported by the SDK and otherwise dropped: a failed stream or a
            # message it could not read. Logged so a "server error" has a cause.
            log.warning("narada voice: session error in %s: %s: %s",
                        session.conversation_id, type(err).__name__, str(err)[:300])

        session.on("user_transcript", on_transcript)
        session.on("error", on_error)
        await session.run()

    async def speak(self, conversation_id, transcript):
        """Stream the reply as it is written: each sentence is spoken as soon
        as the brain finishes it. A filler covers a slow start, the planner's
        line a long job; the final answer is said only if it was not streamed.
        An interruption cancels this generator (the SDK does, on the next
        transcript), which stops the rest of the answer at once."""
        loop = asyncio.get_running_loop()
        queue = asyncio.Queue()
        meta = {}

        def heard(sentence):                       # from the brain's thread
            loop.call_soon_threadsafe(queue.put_nowait, sentence)

        task = asyncio.ensure_future(self.reply_for(conversation_id, transcript,
                                                    on_text=heard, meta=meta))
        started, spoke, filled, told = loop.time(), False, False, False
        getter = None
        try:
            while True:
                getter = asyncio.ensure_future(queue.get())
                wait_s = (max(0.0, FILLER_AFTER_S - (loop.time() - started))
                          if not (spoke or filled) else PLANNER_CHECK_S)
                done, _ = await asyncio.wait({getter, task}, timeout=wait_s,
                                             return_when=asyncio.FIRST_COMPLETED)
                if getter in done:
                    spoke = True
                    yield getter.result()
                    continue
                getter.cancel()
                if task in done:
                    while not queue.empty():         # said before the brain returned
                        spoke = True
                        yield queue.get_nowait()
                    break
                if not spoke and not filled:
                    filled = True
                    yield random.choice(FILLERS) + " "
                elif not told and self._planning(conversation_id):
                    # A request that goes to the planner can take a minute. Said
                    # once, so the silence that follows is understood; the steps
                    # show on the screen (the page asks /api/narada/progress).
                    told = True
                    yield PLANNER_LINE + " "
            said = task.result()
            if not meta.get("streamed"):
                for sentence in _sentences(said or ""):
                    yield sentence
        finally:
            if getter is not None and not getter.done():
                getter.cancel()
            if not task.done():
                task.cancel()      # interrupted: a newer turn replaced this one
