"""The conversation so far, kept across restarts.

Until 2026-10 the agent held the last four turns per user in a dict: a deploy,
a crash or a power cut and Narada had never spoken to anyone. Turns are now
written to disk as they happen and older ones are folded into a running
summary, so a long conversation costs a bounded number of tokens and a
restart costs nothing.

A turn is the whole exchange (the question, any tool calls and their results,
the answer), never just two sentences: with only "turn it on" / "it's on" in
memory the model learned that saying so was enough and started confirming
actions it had never called a tool for.

One file for the house. Keys are who is talking, with a "security:" prefix on
Garuda's address, so a Drishti conversation never leaks into Garuda's.
"""
import logging
import threading
import time

from ..garuda_auto import jsonfile
from . import budget

log = logging.getLogger(__name__)

# Fold the older turns into the summary once this many are held ...
FOLD_AT = 14
# ... keeping this many verbatim. A count, not a token share: the person's own
# idea of recent is "the last few things I said".
KEEP_RECENT = 6
# Never hold more than this, summariser or no summariser.
HARD_CAP = 40
# A conversation left alone this long is over: its turns stop being replayed
# word for word (the summary and the memory carry what mattered).
STALE_AFTER_S = 6 * 3600
TOOL_RESULT_CHARS = 300
SUMMARY_CHARS = 2000

SUMMARY_INSTRUCTION = (
    "You keep the running summary of a conversation between a person and their home "
    "assistant, Narada. Rewrite the summary so it also covers the new turns. Keep what "
    "the person asked for, decided, was told and was promised, and anything they said "
    "about themselves; drop pleasantries and anything that only mattered in the moment. "
    "Write plain sentences in the third person, at most 150 words, no lists, no preamble.")


def _trim(turn):
    """A turn as it is stored: old tool results short, since live state is re-read every turn."""
    out = []
    for m in turn:
        m = {k: v for k, v in m.items() if k in ("role", "content", "tool_calls", "tool_call_id")}
        if m.get("role") == "tool":
            m["content"] = (m.get("content") or "")[:TOOL_RESULT_CHARS]
        out.append(m)
    return out


def _plain(turns):
    """Turns as text for the summariser: what was said, and what was done."""
    lines = []
    for entry in turns:
        for m in entry["messages"]:
            if m["role"] == "user":
                lines.append(f"Person: {m.get('content', '')[:600]}")
            elif m["role"] == "assistant" and m.get("content"):
                lines.append(f"Narada: {m['content'][:600]}")
            elif m["role"] == "assistant" and m.get("tool_calls"):
                names = ", ".join(c.get("function", {}).get("name", "?") for c in m["tool_calls"])
                lines.append(f"(Narada used: {names})")
    return "\n".join(lines)


class Conversations:
    def __init__(self, path=None, *, summarise=None, clock=time.time, background=True):
        """`path` None keeps everything in memory (tests, and an agent with no brain).

        `summarise(previous_summary, transcript) -> str` folds old turns; without
        one, turns past the cap are simply dropped, oldest first.
        """
        self.path = path
        self._summarise = summarise
        self._clock = clock
        self._background = background
        self._lock = threading.RLock()
        self._folding = set()
        data = jsonfile.load(path, {}) if path else {}
        self._data = data if isinstance(data.get("conversations", {}), dict) else {}
        self._data.setdefault("conversations", {})

    # ── reading ───────────────────────────────────────────────────────────────

    def _entry(self, key):
        return self._data["conversations"].setdefault(key, {"summary": "", "turns": []})

    def summary(self, key):
        with self._lock:
            return self._data["conversations"].get(key, {}).get("summary", "")

    def history(self, key, token_budget=budget.HISTORY_TOKENS):
        """Messages to replay before the new question, oldest first."""
        now = self._clock()
        with self._lock:
            turns = [t["messages"] for t in self._data["conversations"].get(key, {}).get("turns", ())
                     if now - t.get("at", now) <= STALE_AFTER_S]
        kept, _ = budget.fit_turns(turns, token_budget)
        return [m for turn in kept for m in turn]

    def summary_block(self, key):
        """The summary as a paragraph for the system prompt, or ''."""
        text = self.summary(key)
        return ("\n\nEarlier in this conversation (a summary; the turns themselves follow "
                f"when they are recent):\n{text}") if text else ""

    # ── writing ───────────────────────────────────────────────────────────────

    def record(self, key, turn):
        with self._lock:
            entry = self._entry(key)
            entry["turns"].append({"at": self._clock(), "messages": _trim(turn)})
            if len(entry["turns"]) > HARD_CAP:
                del entry["turns"][:len(entry["turns"]) - HARD_CAP]
            self._save()
            due = len(entry["turns"]) >= FOLD_AT and key not in self._folding
            if due:
                self._folding.add(key)
        if due:
            if self._background:
                threading.Thread(target=self._fold, args=(key,), daemon=True,
                                 name="narada-summary").start()
            else:
                self._fold(key)

    def forget(self, key):
        with self._lock:
            if self._data["conversations"].pop(key, None) is not None:
                self._save()

    def _save(self):
        if self.path:
            try:
                jsonfile.save(self.path, self._data)
            except OSError:
                log.exception("saving conversations")

    def _fold(self, key):
        """Fold all but the most recent turns into the summary. Never raises."""
        try:
            with self._lock:
                entry = self._data["conversations"].get(key)
                if not entry or len(entry["turns"]) < FOLD_AT:
                    return
                old = list(entry["turns"][:-KEEP_RECENT])
                previous = entry.get("summary", "")
            summary = previous
            if self._summarise:
                try:
                    summary = (self._summarise(previous, _plain(old)) or "").strip()[:SUMMARY_CHARS] or previous
                except Exception as exc:
                    # Keep the turns for the next attempt; the hard cap bounds the file.
                    log.warning("conversation summary failed: %s: %s", type(exc).__name__, exc)
                    return
            with self._lock:
                entry = self._data["conversations"].get(key)
                if not entry:
                    return
                # Only what was summarised is removed: turns may have arrived meanwhile.
                done = {id(t) for t in old}
                entry["turns"] = [t for t in entry["turns"] if id(t) not in done]
                entry["summary"] = summary
                self._save()
        finally:
            with self._lock:
                self._folding.discard(key)
