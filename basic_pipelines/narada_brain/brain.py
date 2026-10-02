"""The brain as the agent sees it: one object, a handful of questions.

    system = brain.system_prompt(user, role, scope=..., key=...)   who Narada is, plus the summary
    past   = brain.history(key)                                     turns to replay
    reply  = brain.check_reply(reply)                               guards, last thing before the person
    brain.record(key, turn)                                         after a turn that reached the model
"""
import logging
import os
from collections import deque
import time

import re

from . import gate, guards, persona
from .conversation import SUMMARY_INSTRUCTION, Conversations
from .memory import CATEGORIES, MEMORY_FILE, MemoryStore

log = logging.getLogger(__name__)

CONVERSATIONS_FILE = "narada_conversations.json"
# The person asked for this to be kept, as opposed to mentioning it in passing.
_ASKED = re.compile(r"\b(remember|note (that|this|it|down)|keep (that|this|it)? ?in mind|don.t forget|"
                    r"make a note|save (that|this))\b", re.I)


class Brain:
    memory_categories = CATEGORIES

    def __init__(self, data_dir=None, chat=None, *, clock=time.time, background=True):
        """`data_dir` None keeps the conversation in memory only. `chat` is the model
        client used for summaries (the same one the agent talks through)."""
        self.chat = chat
        self._clock = clock
        path = os.path.join(data_dir, CONVERSATIONS_FILE) if data_dir else None
        self.conversations = Conversations(path, summarise=self._summarise if chat else None,
                                           clock=clock, background=background)
        self.memory = MemoryStore(os.path.join(data_dir, MEMORY_FILE) if data_dir else None,
                                  clock=clock)
        self.stats = {"leaks_blocked": 0, "facts_saved": 0, "facts_held": 0, "facts_refused": 0}
        # What memory just did, for the chat to show ("Saved to memory", with Undo).
        # A typed reply carries its own; a spoken one is fetched from here, because
        # the voice service relays Narada's words and nothing else.
        self._events = deque(maxlen=50)

    # ── what goes into a turn ─────────────────────────────────────────────────

    def system_prompt(self, user, role, *, scope="home", key=None, query=""):
        system = persona.system_prompt(user, role, scope=scope, now=self._clock())
        system += self.memory.context_block(query)
        if key:
            system += self.conversations.summary_block(key)
        return system

    def history(self, key):
        return self.conversations.history(key)

    # ── what comes out of one ─────────────────────────────────────────────────

    def check_reply(self, reply, *, voice=False):
        reply, leaked = guards.protect(reply, persona.protected_text())
        if leaked:
            self.stats["leaks_blocked"] += 1
            log.warning("Narada reply recited its instructions; replaced")
        return guards.fit_spoken(reply) if voice else reply

    def record(self, key, turn):
        self.conversations.record(key, turn)

    def forget(self, key):
        self.conversations.forget(key)

    # ── memory ────────────────────────────────────────────────────────────────

    def remember_fact(self, text, *, said="", user="", category=None, replaces=None):
        """A fact the model wants kept, written through the gate.

        Saved at once when its words trace to what the person just said; held
        for their confirmation when they do not (the model concluded it, or was
        steered by something it read). Returns the store's outcome.
        """
        names = [user] + [f["text"] for f in self.memory.facts() if f["category"] == "person"]
        theirs = gate.traces_to(text, said, names=names)
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
            max_tokens=400, temperature=0.2, timeout=30)
        return message.get("content") or ""

    def status(self):
        return dict(self.stats)
