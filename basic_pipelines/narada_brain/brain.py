"""The brain as the agent sees it: one object, a handful of questions.

    system = brain.system_prompt(user, role, scope=..., key=...)   who Narada is, plus the summary
    past   = brain.history(key)                                     turns to replay
    reply  = brain.check_reply(reply)                               guards, last thing before the person
    look   = brain.look_again(text)                                 Jev's second look at house data
    brain.record(key, turn)                                         after a turn that reached the model
"""
import concurrent.futures
import logging
import os
from collections import OrderedDict, deque
import time

import re
import threading

from ..garuda_auto.llm import NO_THINKING
from . import distiller, gate, guards, persona
from .conversation import SUMMARY_INSTRUCTION, Conversations
from .memory import CATEGORIES, MEMORY_FILE, MemoryStore
from .noticing import NOTICES_FILE, Noticer
from .observer import OFFERS_FILE, REFRESH_S, Observer

log = logging.getLogger(__name__)

CONVERSATIONS_FILE = "narada_conversations.json"
# A conversation left alone this long is read back for facts worth keeping.
DISTILL_IDLE_S = 180
# ... and so is a long one that never pauses, every this many unread turns.
DISTILL_EVERY = 8
# The person asked for this to be kept, as opposed to mentioning it in passing.
# The person said something about themselves or their people: a fact may have
# gone past that the model did not save.
_ABOUT_SELF = re.compile(r"\b(i|i.m|i.ve|i.d|my|we|we.re|our)\b", re.I)
# Jev's second look at house data (look_again): at most this many strings to a
# call (8 took 0.6 s from the Pi), and the verdicts on this many kept, since a
# house's names and rules come back every turn and need asking once.
LOOK_BATCH = 8
LOOK_KEEP = 2048
_ASKED = re.compile(r"\b(remember|note (that|this|it|down)|keep (that|this|it)? ?in mind|don.t forget|"
                    r"make a note|save (that|this))\b", re.I)


class Look:
    """One second look under way (Brain.look_again)."""

    def __init__(self, futures):
        self._futures, self._found = futures, ""
        self._started = time.monotonic()

    @property
    def done(self):
        return all(f.done() for f in self._futures)

    def found(self, budget):
        """The string that steers the assistant, or '': waited for until `budget`
        seconds after the look began, then whatever has come back."""
        if self._found:
            return self._found
        concurrent.futures.wait(self._futures,
                                timeout=max(0.0, self._started + budget - time.monotonic()))
        for f in self._futures:
            if f.done() and f.result():
                self._found = f.result()
                break
        return self._found


class Brain:
    memory_categories = CATEGORIES

    def __init__(self, data_dir=None, chat=None, *, clock=time.time, background=True,
                 read_back=None):
        """`data_dir` None keeps the conversation in memory only. `chat` is the model
        client used for summaries (the same one the agent talks through).
        `read_back` reads a turn with a missed fact back for facts (default: only
        when `background`; with background off it runs before record returns)."""
        self.chat = chat
        self._clock = clock
        # facts -> {fact: P(it stays true)}, a second look at what the
        # distiller proposes (Jev; set by Garuda_web). None: regex only.
        self.judge = None
        # strings -> {string: P(it steers the assistant)}, a second look at the
        # house data the agent reads (guards.pieces; Jev, set by Garuda_web).
        # None: the pattern only.
        self.screen = None
        self._looked = OrderedDict()
        self._look_lock = threading.Lock()
        self._look_pool = None
        path = os.path.join(data_dir, CONVERSATIONS_FILE) if data_dir else None
        self.conversations = Conversations(path, summarise=self._summarise if chat else None,
                                           clock=clock, background=background)
        self.memory = MemoryStore(os.path.join(data_dir, MEMORY_FILE) if data_dir else None,
                                  clock=clock)
        self.observer = Observer(self.memory, os.path.join(data_dir, OFFERS_FILE) if data_dir else None,
                                 clock=clock)
        self.noticer = Noticer(self.memory, os.path.join(data_dir, NOTICES_FILE) if data_dir else None,
                               clock=clock)
        self._habits, self._habits_at = [], None
        self.stats = {"leaks_blocked": 0, "facts_saved": 0, "facts_held": 0, "facts_refused": 0}
        # What memory just did, for the chat to show ("Saved to memory", with Undo).
        # A typed reply carries its own; a spoken one is fetched from here, because
        # the voice service relays Narada's words and nothing else.
        self._events = deque(maxlen=50)
        self._background = background
        self._read_back = background if read_back is None else read_back
        self._timers = {}
        self._timer_lock = threading.Lock()
        if background and chat is not None:
            # Turns a restart interrupted are read back once the service has settled.
            for key in self.conversations.keys():
                if self.conversations.undistilled(key):
                    self._schedule_distill(key, delay=60)

    # ── what goes into a turn ─────────────────────────────────────────────────

    def system_prompt(self, user, role, *, scope="home", key=None, query="", flags=None):
        """`flags`, when given, gets "injection": the instruction-like text found
        in the household memory, which any signed-in person can add to."""
        system = persona.system_prompt(user, role, scope=scope, now=self._clock())
        block = self.memory.context_block(query)
        hit = self.scan(block)
        if hit:
            block = guards.wrap(block)
        if flags is not None:
            flags["injection"] = hit
        system += block
        if key:
            system += self.conversations.summary_block(key)
        return system

    def history(self, key):
        return self.conversations.history(key)

    # ── what comes out of one ─────────────────────────────────────────────────

    def scan(self, text):
        """Whether data read this turn carries an instruction (see guards.injected),
        or a string a second look already found to (look_again)."""
        hit = guards.injected(text) or self._known_steering(text)
        if hit:
            self.stats["injections"] = self.stats.get("injections", 0) + 1
            log.warning("instruction-like text in house data: %r", hit[:80])
        return hit

    def _known_steering(self, text):
        if not self._looked:
            return ""
        with self._look_lock:
            return next((s for s in guards.pieces(text)
                         if self._looked.get(s, 0.0) >= guards.STEER_AT), "")

    def look_again(self, text):
        """Jev's second look at data scan() passed: a Look whose found() is the
        first string in it that steers the assistant, or ''. None when there is
        nothing to ask (no screen, or every string in it already judged). It
        runs in the background, while the model reads the text."""
        if self.screen is None:
            return None
        with self._look_lock:
            new = [s for s in guards.pieces(text) if s not in self._looked]
        if not new:
            return None
        if self._look_pool is None:
            self._look_pool = concurrent.futures.ThreadPoolExecutor(4, thread_name_prefix="look")
        return Look([self._look_pool.submit(self._screen, new[i:i + LOOK_BATCH])
                     for i in range(0, len(new), LOOK_BATCH)])

    def _screen(self, strings):
        try:
            seen = self.screen(strings)
        except Exception as exc:
            self.stats["look_errors"] = self.stats.get("look_errors", 0) + 1
            log.warning("second look at house data failed: %s", exc)
            return ""
        self.stats["looks"] = self.stats.get("looks", 0) + 1
        with self._look_lock:
            for s in strings:
                if s in seen:
                    self._looked[s] = seen[s]
            while len(self._looked) > LOOK_KEEP:
                self._looked.popitem(last=False)
        hit = next((s for s in strings if seen.get(s, 0.0) >= guards.STEER_AT), "")
        if hit:
            self.stats["injections"] = self.stats.get("injections", 0) + 1
            log.warning("instruction-like text in house data (second look): %r", hit[:80])
        return hit

    def check_reply(self, reply, *, voice=False):
        reply, leaked = guards.protect(reply, persona.protected_text())
        if leaked:
            self.stats["leaks_blocked"] += 1
            log.warning("Narada reply recited its instructions; replaced")
        return guards.fit_spoken(reply) if voice else reply

    def record(self, key, turn, generation=None):
        if not self.conversations.record(key, turn, generation=generation):
            return                      # cleared while it was being answered
        if self.chat is None:
            return
        # Some models skip remember_fact when a fact comes up in passing
        # ("I'm vegetarian, by the way. Dinner ideas?"): nemotron-3.5-super-vl
        # saved 3 of 9 such facts on its own (2026-10-17). Waiting for the chat
        # to go quiet is too late when the next question needs the fact, so
        # such a turn is read back at once.
        missed = self._read_back and _missed_fact(turn)
        if not self._background:
            if missed:
                self._distill_quietly(key)
            return
        due_now = missed or len(self.conversations.undistilled(key)) >= DISTILL_EVERY
        self._schedule_distill(key, delay=1 if due_now else DISTILL_IDLE_S)

    # ── reading a conversation back for what was missed ───────────────────────

    def _schedule_distill(self, key, delay):
        """(Re)start the idle clock for this conversation: each new turn pushes it back."""
        with self._timer_lock:
            old = self._timers.pop(key, None)
            if old is not None:
                old.cancel()
            timer = threading.Timer(delay, self._distill_quietly, args=(key,))
            timer.daemon = True
            timer.name = "narada-distill"
            self._timers[key] = timer
            timer.start()

    def _distill_quietly(self, key):
        try:
            self.distill(key)
        except Exception:
            log.exception("distilling %s", key)

    def distill(self, key):
        """Read this conversation's unread turns and keep what was missed.

        Returns the facts written. Turns are marked read only when the model
        answered, so a failed call is retried with the next turn or restart.
        """
        if self.chat is None or not getattr(self.chat, "configured", True):
            return []
        turns = self.conversations.undistilled(key)
        if not turns:
            return []
        speaker = key.split(":", 1)[-1] or "the person"
        known = [f["text"] for f in self.memory.facts()]
        names = [f["text"] for f in self.memory.facts() if f["category"] == "person"]
        found = distiller.distill(turns, speaker=speaker, chat=self.chat, known=known, names=names,
                                  judge=self.judge)
        if found is None:
            return []
        self.conversations.mark_distilled(turns)
        written = []
        for text, category, held in found:
            outcome = self.memory.remember(text, category=category, origin="distilled", by=speaker,
                                           pending=held)
            if outcome["status"] in ("saved", "updated", "pending"):
                self.stats["facts_held" if outcome["status"] == "pending" else "facts_saved"] += 1
                self._event(outcome["fact"], outcome["status"], outcome["replaced"])
                written.append(outcome["fact"])
        if written:
            log.info("distilled %d fact(s) from the conversation with %s", len(written), speaker)
        return written

    def forget(self, key):
        self.conversations.forget(key)
        with self._timer_lock:
            timer = self._timers.pop(key, None)
        if timer is not None:
            timer.cancel()

    # ── memory ────────────────────────────────────────────────────────────────

    def remember_fact(self, text, *, said="", user="", category=None, replaces=None,
                      untrusted=False):
        """A fact the model wants kept, written through the gate.

        Saved at once when its words trace to what the person just said; held
        for their confirmation when they do not (the model concluded it, or was
        steered by something it read). `untrusted` is set once the turn has read
        text that looked like an instruction: after that nothing is saved without
        the person, even a fact that would have traced. Returns the store's outcome.
        """
        names = [user] + [f["text"] for f in self.memory.facts() if f["category"] == "person"]
        # Traced to what they told, not what they asked: a request restated as
        # a fact ("Admin wants the lamp on at 7") is held for them to confirm.
        theirs = gate.traces_to(text, gate.statements(said), names=names) and not untrusted
        outcome = self.memory.remember(
            text, category=category, by=user, replaces=replaces, pending=not theirs,
            origin="asked" if _ASKED.search(said or "") else "noticed")
        key = {"saved": "facts_saved", "updated": "facts_saved", "pending": "facts_held",
               "rejected": "facts_refused"}.get(outcome["status"])
        if key:
            self.stats[key] += 1
        if outcome["status"] in ("saved", "updated", "pending"):
            outcome["event"] = self._event(outcome["fact"], outcome["status"], outcome["replaced"])
        return outcome

    # ── learning from what the household does ─────────────────────────────────

    def observe(self, fetch, names, *, may_offer=False):
        """Keep memory in step with the routines the house has noticed.

        `fetch()` returns the house's current routine suggestions (it reads the
        actuation log, so it is called at most every few minutes); `names`
        maps device ids to names. Returns one routine to offer in the
        conversation, or None. Never raises: this rides along with a turn and
        must not be the reason one fails.
        """
        try:
            now = self._clock()
            if self._habits_at is None or now - self._habits_at >= REFRESH_S:
                self._habits, self._habits_at = list(fetch() or []), now
                for outcome in self.observer.sync(self._habits, names):
                    self.stats["facts_saved"] += 1
                    self._event(outcome["fact"], outcome["status"], outcome["replaced"])
            return self.observer.offer(self._habits) if may_offer else None
        except Exception:
            log.exception("observing routines")
            return None

    def notice(self, snapshot_fn, *, scope="home"):
        """One thing worth saying that nobody asked about, or None (see noticing.py).

        Never raises: like observe(), it rides along with a turn.
        """
        try:
            found = self.noticer.notice(snapshot_fn, scope=scope)
            if found:
                self.stats["noticed"] = self.stats.get("noticed", 0) + 1
            return found
        except Exception:
            log.exception("noticing")
            return None

    def mute_notice(self, key, names, by=""):
        """ "Don't tell me this": kept as the household's choice. Returns the outcome or None."""
        outcome = self.noticer.mute(key, names, by=by)
        if outcome and outcome["status"] in ("saved", "updated"):
            outcome["event"] = self._event(outcome["fact"], outcome["status"], outcome["replaced"])
        return outcome

    def routine_decided(self, suggestion, name, accepted, by=""):
        """The household answered an offered routine, on whichever page."""
        outcome = self.observer.decided(suggestion, name, accepted, by=by)
        self._habits_at = None                       # what is on offer has changed
        if outcome["status"] in ("saved", "updated"):
            self.stats["facts_saved"] += 1
            outcome["event"] = self._event(outcome["fact"], outcome["status"], outcome["replaced"])
        return outcome

    def forget_fact(self, what):
        """Returns (fact, event), or (None, None) when nothing matched."""
        fact = self.memory.forget(what)
        return (fact, self._event(fact, "forgotten")) if fact else (None, None)

    def _event(self, fact, status, replaced=None):
        event = {"id": fact["id"], "text": fact["text"], "status": status,
                 "replaced": (replaced or {}).get("text"), "replaced_id": (replaced or {}).get("id"),
                 "at": self._clock()}
        self._events.append(event)
        return event

    def events_since(self, since):
        return [dict(e) for e in self._events if e["at"] > since]

    # ── internals ─────────────────────────────────────────────────────────────

    def _summarise(self, previous, transcript):
        message = self.chat.chat(
            [{"role": "system", "content": SUMMARY_INSTRUCTION},
             {"role": "user", "content": f"Summary so far:\n{previous or '(none yet)'}\n\n"
                                         f"New turns:\n{transcript}"}],
            max_tokens=500, temperature=0.2, timeout=30, extra=NO_THINKING)
        return message.get("content") or ""

    def status(self):
        return dict(self.stats)


def _missed_fact(turn):
    """The person spoke about themselves this turn and memory was not touched."""
    said = " ".join(str(m.get("content") or "") for m in turn if m.get("role") == "user")
    if not _ABOUT_SELF.search(said):
        return False
    for m in turn:
        for call in m.get("tool_calls") or ():
            # Saved, or asked to forget: reading the turn back could put the
            # forgotten fact straight back.
            if (call.get("function") or {}).get("name") in ("remember_fact", "forget_fact"):
                return False
    return True
