"""Checks that are code, because prose in a prompt is not a control.

The bREADth verification measured this directly: a persona that forbade
reciting itself was recited ten times out of ten, and here the baseline run
(2026-10-02) had Narada print its system prompt verbatim on request. The
persona still asks the model not to; this is what makes it true.
"""
import re

LEAK_REPLY = ("I keep my internal instructions to myself. I can tell you what I can do, "
              "though: answer questions, chat, and run the house when you ask.")
# A sentence shorter than this is too ordinary to be evidence ("No emojis.").
_MIN_SENTENCE = 40
# One long instruction sentence can turn up in an honest answer about what
# Narada does; two verbatim is recitation.
_LEAK_SENTENCES = 2


# A spoken reply is synthesised and billed by the character, and a long one is
# a monologue. The persona asks for under 200; this is the ceiling.
SPOKEN_CHARS = 300


def fit_spoken(reply, limit=SPOKEN_CHARS):
    """A spoken reply cut to whole sentences within `limit`.

    Whole sentences or none, like everything else here: a voice that stops in
    the middle of a clause sounds broken. If even the first sentence is too
    long it is cut at a word and closed, which is the least bad option.
    """
    reply = (reply or "").strip()
    if len(reply) <= limit:
        return reply
    kept = ""
    for sentence in re.findall(r"[^.!?]+[.!?]+[\"')\]]*\s*|[^.!?]+$", reply):
        if len(kept) + len(sentence.rstrip()) > limit:
            break
        kept += sentence
    if kept.strip():
        return kept.strip()
    return reply[:limit].rsplit(" ", 1)[0].rstrip(",;:") + "."


def _norm(text):
    return re.sub(r"\s+", " ", (text or "").lower()).strip()


def _sentences(text):
    parts = re.split(r"(?<=[.:;])\s+|\n+", text or "")
    return [n for n in (_norm(p).strip(" -") for p in parts) if len(n) >= _MIN_SENTENCE]


def leaked_sentences(reply, protected):
    """How many of the protected instruction sentences appear verbatim in `reply`."""
    flat = _norm(reply)
    return sum(1 for s in set(_sentences(protected)) if s in flat)


def protect(reply, protected):
    """Replace a reply that recites the instructions. Returns (reply, leaked?).

    This one replaces rather than annotates: the text IS the disclosure, and a
    warning printed under a leaked configuration has already leaked it. It
    matches verbatim reproduction only. A model that paraphrases its
    instructions gets through, and no string check can do otherwise; what a
    paraphrase gives away is what Narada does, which is not a secret.
    """
    if leaked_sentences(reply, protected) >= _LEAK_SENTENCES:
        return LEAK_REPLY, True
    return reply, False
