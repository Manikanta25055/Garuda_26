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

from .speech_text import for_speech


log = logging.getLogger(__name__)

TTS_MODEL = "eleven_v4_turbo"     # scripts/setup_narada_voice.py configures the engine with it
INFO_TTL_S = 60                  # the info panel must not hammer the ElevenLabs API
BINDING_TTL_S = 15 * 60          # longer than the 10-minute conversation cap
UNKNOWN_REPLY = "This conversation was not started from Garuda. Please reopen Narada."
# How ElevenLabs treats the reply stream, measured 2026-10-15 against a scratch
# engine (the reason for every number below):
# - After each chunk it waits the engine's cascade_timeout_seconds (now 15, was
#   4) for the next. Past that it gives up on the attempt and sends the same
#   transcript again (same event_id, which the SDK drops as a duplicate), and
#   after two retries it ends the call: "Generating the LLM response took too
#   long", the browser's "Server error". A blank chunk resets the wait and is
#   never said, so a long silence is bridged with one every KEEPALIVE_S.
# - It holds short text back until about a sentence's worth has come or the
#   response ends: "Mm, one sec." alone was never heard while Narada thought.
#   Each filler is a whole sentence of 50+ characters, which is said at once.
# - A response ends at its final message; nothing sent after it is said, and
#   ElevenLabs cuts one off about 48 s in (what came later was dropped). So a
#   slow turn, the planner's included, is held open up to HOLD_S and its answer
#   said when it comes; past that Narada says it is still going, the spoken
#   turn ends, and the answer goes to the chat when it is ready (the page polls
#   /api/narada/voice/turns).
# The model's first sentence usually comes within 1-4 s. A filler that
# starts just before it only pushes the answer back by its own length
# (measured end to end: filler at 1.8 s, answer at 2.1 s; at 3.0 s, answer
# at 3.4 s), so it is kept for turns that wait on tools. The screen says
# "Thinking" from the moment the question is in.
FILLER_AFTER_S = 4.5
PLANNER_CHECK_S = 1.0
KEEPALIVE_S = 5.0
HOLD_S = 35.0
PLANNER_LINE = "This one takes some building, so give me a little while to put it together."
LATER_LINE = "It's still going, so I'll put the answer on your screen as soon as it's ready."
FILLERS = ("Okay, give me a second, I'm just checking that for you.",
           "Sure, let me look into that, it'll only take a moment.",
           "Right, one moment while I pull that up for you.",
           "Hmm, let me check on that, give me just a second.",
           "Okay, I'm on it, just give me a moment to look.")


def _guards():
    # Imported when first needed: scripts/setup_narada_voice.py imports this
    # module on its own, where the package (and narada_brain) is not there.
    from ..narada_brain import guards
    return guards


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
        # Whether the brain can hand its answer over as it writes it, and be
        # stopped when the person talks over it.
        try:
            params = list(inspect.signature(reply_fn).parameters.values())
            anykw = any(p.kind is p.VAR_KEYWORD for p in params)
            self._streams = anykw or any(p.name == "on_text" for p in params)
            self._cancels = anykw or any(p.name == "cancel" for p in params)
        except (TypeError, ValueError):
            self._streams = self._cancels = False   # (text, user, role, scope) -> dict
        self._on_turn = on_turn            # (user, heard, said) -> None, for the logs
        self._clock = clock
        self._client = client
        self._lock = threading.Lock()
        self._bindings = {}                # conversation_id -> (user, role, scope, expires)
        self._turns = {}                   # bounded display results, separate from speech
        # conversation_id -> {"n", "text"}: the last question heard, the moment
        # it arrives. The page shows it and closes the microphone at once; the
        # speech service tells the browser about a turn ~3 s later (measured).
        self._heard = {}
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
            self._heard = {k: v for k, v in self._heard.items() if k in self._bindings}
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

    def heard_for(self, conversation_id, user, role, scope):
        """The last question heard in a conversation, for its bound caller only."""
        with self._lock:
            bound = self._bindings.get(conversation_id)
            if not bound or bound[3] <= self._clock() or bound[:3] != (user, role, scope):
                return None
            return dict(self._heard[conversation_id]) if conversation_id in self._heard else None

    def _note_heard(self, conversation_id, transcript):
        heard = next((m.content for m in reversed(transcript) if m.role == "user"), "").strip()
        if not re.search(r"\w", heard) or self.binding(conversation_id) is None:
            return
        with self._lock:
            n = self._heard.get(conversation_id, {}).get("n", 0) + 1
            self._heard[conversation_id] = {"n": n, "text": heard[:2000]}

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

    async def reply_for(self, conversation_id, transcript, on_text=None, meta=None, cancel=None):
        """What to say for the latest user turn of one conversation: the
        spoken part of the answer. The whole answer is kept for the chat.

        With `on_text`, the brain hands over the spoken part a sentence at a
        time as it is written; meta["streamed"] is then whether it already
        went that way (so it is not said twice). `cancel`, a threading.Event,
        stops the brain at its next step when this turn is replaced.
        """
        heard = next((m.content for m in reversed(transcript) if m.role == "user"), "").strip()
        if not re.search(r"\w", heard):
            # ElevenLabs sends "..." for a breath or a noise; it was answered
            # with a greeting nobody asked for (seen in the voice log).
            return None
        bound = self.binding(conversation_id)
        if bound is None:
            log.warning("narada voice: transcript for unbound conversation %s", conversation_id)
            return UNKNOWN_REPLY
        user, role, scope = bound
        kw = {}
        if on_text and self._streams:
            kw["on_text"] = on_text
        if cancel is not None and self._cancels:
            kw["cancel"] = cancel
        try:
            result = await asyncio.to_thread(self._reply_fn, heard, user, role, scope, **kw)
            if result.get("lane") == "cancelled":
                return None                 # replaced by a newer turn: nothing to show
            reply = result.get("reply", "")
            # The agent has already split its answer into the spoken part and
            # the shown one; anything else (a taught phrase) is split here.
            said = _spoken(reply if "display_reply" in result else _guards().split_spoken(reply)[0])
            if meta is not None:
                meta["streamed"] = bool(result.get("streamed"))
        except Exception:
            log.exception("narada voice: reply failed")
            said = "Something went wrong. Please check the current state before trying again."
            result = {"reply": said}
        # Keep the checked Markdown and cards before the speech service flattens
        # them. The browser reads this even while audio is still playing.
        with self._lock:
            turns = self._turns.setdefault(conversation_id, [])
            turns.append({"id": turns[-1]["id"] + 1 if turns else 1, "heard": heard,
                          "response": result.get("display_reply") or _guards().strip_more(result.get("reply"))
                          or "Done.",
                          **{k: result[k] for k in ("lane", "actions", "proposal", "memory", "offer",
                                                   "observation", "confirm", "artifacts", "steps",
                                                   "images", "planner", "route", "model", "summary")
                             if k in result}})
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
        """Say the spoken part of the answer as it is written, a sentence at a
        time, and end the turn as soon as it is said: the details are still
        being written, for the chat. A filler covers a slow start, a blank
        chunk keeps ElevenLabs waiting through tool calls, and a job that runs
        long is told once and handed to the screen. An interruption cancels
        this generator (the SDK does, on the next transcript), which stops the
        speech at once and the brain at its next step."""
        loop = asyncio.get_running_loop()
        queue = asyncio.Queue()
        meta = {}
        cancel = threading.Event()

        def heard(sentence):                       # from the brain's thread; None = spoken part over
            loop.call_soon_threadsafe(queue.put_nowait, sentence)

        self._note_heard(conversation_id, transcript)
        task = asyncio.ensure_future(self.reply_for(conversation_id, transcript, on_text=heard,
                                                    meta=meta, cancel=cancel))
        started = last_out = loop.time()
        spoke = filled = handed_over = told = False
        getter = None
        try:
            while True:
                now = loop.time()
                if not (spoke or filled):
                    wait_s = FILLER_AFTER_S - (now - started)
                else:
                    wait_s = min(PLANNER_CHECK_S, KEEPALIVE_S - (now - last_out))
                getter = asyncio.ensure_future(queue.get())
                done, _ = await asyncio.wait({getter, task}, timeout=max(0.0, wait_s),
                                             return_when=asyncio.FIRST_COMPLETED)
                now = loop.time()
                if getter in done:
                    sentence = getter.result()
                    if sentence is None:
                        handed_over = not task.done()
                        break                       # the spoken part is over; the rest is for the chat
                    spoke = True
                    last_out = now
                    yield for_speech(sentence)
                    continue
                getter.cancel()
                if task in done:
                    while not queue.empty():        # said before the brain returned
                        sentence = queue.get_nowait()
                        if sentence is None:
                            break
                        spoke = True
                        yield for_speech(sentence)
                    break
                if not told and self._planning(conversation_id):
                    # The planner can take a while; said once (instead of a
                    # filler), and its steps show on the screen meanwhile.
                    told = filled = True
                    last_out = now
                    yield PLANNER_LINE + " "
                elif not spoke and not filled:
                    filled = True
                    last_out = now
                    yield random.choice(FILLERS) + " "
                elif now - started >= HOLD_S:
                    handed_over = True
                    yield LATER_LINE
                    break
                elif now - last_out >= KEEPALIVE_S:
                    last_out = now
                    yield " "
            if not handed_over and task.done():
                said = task.result()
                if said and not meta.get("streamed"):
                    for sentence in _sentences(said):
                        yield for_speech(sentence)
        finally:
            if getter is not None and not getter.done():
                getter.cancel()
            # Interrupted: a newer turn replaced this one, and it stops. A job
            # the planner has started is the person's request, not a sentence
            # being said: it goes on, and its answer reaches the chat.
            if not task.done() and not handed_over and not told:
                cancel.set()
                task.cancel()
