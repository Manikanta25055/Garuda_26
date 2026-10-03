"""The pass that reads a conversation back and keeps what was missed.

`remember_fact` covers what the model noticed WHILE talking. This covers what
it did not: someone mentions in passing that their sister is allergic to
peanuts, the model answers the actual question, and nobody has recorded the
one lasting thing that was said. One short model call after the conversation
has gone quiet reads it back and extracts what is worth keeping.

Everything here assumes the model's output is hostile. It is derived from free
text a person typed, it is written to the household's record, and it is then
replayed into every future prompt: exactly the shape of an injection chain. So
the parser is strict and bounded and silently discards anything it does not
fully understand, and a candidate is kept only if its words trace to what the
person themselves said (gate.traces_to). A distillation that produces nothing
is a non-event; one that produces a bad fact is a wrong line in the record.
Unlike a save made during the chat there is nobody at the screen to ask, so a
fact that does not trace is dropped, not held.
"""
import json
import logging

from ..garuda_auto.llm import NO_THINKING
from . import gate
from .memory import CATEGORIES

log = logging.getLogger(__name__)

# Most facts one pass may produce. A chat that appears to hold twenty lasting
# facts has been misread.
MAX_CANDIDATES = 8
MAX_TURNS = 20
MAX_TURN_CHARS = 500
# A reply longer than this is not an extraction result.
MAX_RESPONSE_CHARS = 8000

INSTRUCTION = (
    "You read a finished conversation between a person and their home assistant and list "
    "the lasting facts the PERSON stated about themselves, the people in their household, "
    "their habits and preferences, or what they call things in the house.\n"
    "Rules:\n"
    "- Only what the person said. Never what the assistant said, suggested or assumed.\n"
    "- Only things that will still be true next month. Not moods, one-off requests, "
    "questions, or what a device is doing now.\n"
    "- Never passwords, codes, keys or numbers of cards and accounts.\n"
    "- One plain sentence per fact, naming who it is about (use the given speaker name for "
    "'I'), never 'I' or 'you'.\n"
    "- Skip anything already in the known list.\n"
    f"- At most {MAX_CANDIDATES} facts. If there is nothing lasting, return [].\n"
    'Reply with a JSON array only, each item {"text": "...", "category": one of '
    + json.dumps(list(CATEGORIES)) + "}. No prose, no code fence.")


def transcript(turns, speaker):
    """(text for the extractor, everything the person said) from stored turns."""
    lines, said = [], []
    for entry in turns[-MAX_TURNS:]:
        for m in entry["messages"]:
            text = " ".join((m.get("content") or "").split())
            if not text:
                continue
            if m["role"] == "user":
                said.append(text)
                lines.append(f"{speaker}: {text[:MAX_TURN_CHARS]}")
            elif m["role"] == "assistant":
                lines.append(f"Assistant: {text[:MAX_TURN_CHARS]}")
    return "\n".join(lines), "\n".join(said)


def parse_candidates(raw):
    """Pull [(text, category)] out of whatever the model returned.

    Bounded at every step and forgiving of nothing: a response that is too
    long, is not an array, or holds elements of the wrong shape yields what
    was understood and no more.
    """
    if not isinstance(raw, str) or len(raw) > MAX_RESPONSE_CHARS:
        return []
    # Models fence JSON even when told not to, and some prepend a sentence:
    # take the outermost bracket pair and ignore the rest.
    start, end = raw.find("["), raw.rfind("]")
    if start < 0 or end <= start:
        return []
    try:
        array = json.loads(raw[start:end + 1])
    except ValueError:
        return []
    if not isinstance(array, list):
        return []
    out, seen = [], set()
    for item in array[:MAX_CANDIDATES * 2]:
        if isinstance(item, str):
            text, category = item, None          # a bare string, which small models emit
        elif isinstance(item, dict) and isinstance(item.get("text"), str):
            text, category = item["text"], item.get("category")
        else:
            continue
        clean, _ = gate.clean_fact(text)
        if clean is None or clean.lower() in seen:
            continue
        seen.add(clean.lower())
        out.append((clean, category if category in CATEGORIES else None))
        if len(out) == MAX_CANDIDATES:
            break
    return out


def distill(turns, *, speaker, chat, known, names=()):
    """Facts worth keeping from these turns: [(text, category)]. Never raises."""
    text, said = transcript(turns, speaker)
    if not said.strip():
        return []
    try:
        message = chat.chat(
            [{"role": "system", "content": INSTRUCTION},
             {"role": "user", "content": f"Speaker name: {speaker}\n\nAlready known:\n"
                                         + ("\n".join(f"- {k}" for k in known) or "(nothing)")
                                         + f"\n\nConversation:\n{text}"}],
            # Without this a reasoning model spends the whole allowance thinking
            # and returns an empty answer, which reads as "nothing worth keeping".
            max_tokens=700, temperature=0, timeout=30, extra=NO_THINKING)
    except Exception as exc:
        log.warning("distillation failed: %s: %s", type(exc).__name__, exc)
        return None                                  # try these turns again later
    raw = message.get("content") or ""
    if not raw.strip():
        log.warning("distillation returned nothing at all; will retry")
        return None
    candidates = parse_candidates(raw)
    return [(fact, category) for fact, category in candidates
            if gate.traces_to(fact, said, names=[speaker, *names])]
