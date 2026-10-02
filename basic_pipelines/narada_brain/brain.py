"""The brain as the agent sees it: one object, a handful of questions.

    system = brain.system_prompt(user, role, scope=..., key=...)   who Narada is, plus the summary
    past   = brain.history(key)                                     turns to replay
    reply  = brain.check_reply(reply)                               guards, last thing before the person
    brain.record(key, turn)                                         after a turn that reached the model
"""
import logging
import os
import time

from . import guards, persona
from .conversation import SUMMARY_INSTRUCTION, Conversations

log = logging.getLogger(__name__)

CONVERSATIONS_FILE = "narada_conversations.json"


class Brain:
    def __init__(self, data_dir=None, chat=None, *, clock=time.time, background=True):
        """`data_dir` None keeps the conversation in memory only. `chat` is the model
        client used for summaries (the same one the agent talks through)."""
        self.chat = chat
        self._clock = clock
        path = os.path.join(data_dir, CONVERSATIONS_FILE) if data_dir else None
        self.conversations = Conversations(path, summarise=self._summarise if chat else None,
                                           clock=clock, background=background)
        self.stats = {"leaks_blocked": 0}

    # ── what goes into a turn ─────────────────────────────────────────────────

    def system_prompt(self, user, role, *, scope="home", key=None):
        system = persona.system_prompt(user, role, scope=scope, now=self._clock())
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
