"""What may be written to Narada's memory, and when a person has to agree first.

A fact is free text that will be replayed into every future prompt. That is
precisely the shape of an injection chain: text someone influenced goes in
once and steers the model for ever after. So what is stored is checked here,
strictly, and anything doubtful is refused rather than tidied up: a fact that
is not saved is a non-event, a bad one is a wrong line in the household's
record.

Two separate questions:

  clean_fact()   is this a thing that may be stored at all?
  traces_to()    did the person actually say it? If not, the save is held
                 until they confirm it (the chip asks instead of informing).
"""
import re

MAX_CHARS = 200
MIN_CHARS = 4

# Never stored, whoever asks: a memory is read back into prompts that leave the
# house, and is shown on a page any signed-in person can open.
_SECRET = re.compile(
    r"\b(pass\s?word|passcode|pass\s?phrase|otp|one[- ]time (code|password)|\bpin\b (is|number|code)|"
    r"cvv|cvc|api[ _-]?key|secret key|private key|access token|master key|recovery (code|phrase)|"
    r"seed phrase)\b|\b(?:\d[ -]?){13,19}\b|\b(sk|pk|ghp|xox[bap])[-_][A-Za-z0-9_-]{12,}",
    re.I)
# Text aimed at the assistant rather than about a person.
_INSTRUCTION = re.compile(
    r"\b(system (override|prompt|message|instruction)|override|ignore (all|any|the|your|previous|prior)|"
    r"disregard|jailbreak|developer mode|you (must|should|will|are to|have to) (always|never|now)|"
    r"from now on (you|always|never)|always (turn|switch|call|run|open|unlock|disable|enable|say|reply|respond)|"
    r"never (alert|warn|notify|tell|refuse)|whenever (anyone|someone|somebody|a person) says|"
    r"(call|use|invoke|run) (the )?(tool|function)|tool[_ ]call|admin (override|mode|access)|"
    r"instructions? (above|below|to the (assistant|model))|as an? (ai|assistant|language model))\b",
    re.I)
_MARKUP = re.compile(r"[<>{}`\\]|\[\[|\]\]|https?://|www\.", re.I)


def clean_fact(text):
    """(normalised sentence, "") when it may be stored, else (None, why not)."""
    flat = re.sub(r"\s+", " ", str(text or "")).strip().strip("\"'").strip()
    if len(flat) < MIN_CHARS:
        return None, "there is nothing to remember in that"
    if "\n" in str(text or "").strip():
        return None, "a fact is one sentence"
    if len(flat) > MAX_CHARS:
        return None, f"a fact is one short sentence (under {MAX_CHARS} characters)"
    if _SECRET.search(flat):
        return None, "passwords, codes, keys and card numbers are never kept in memory"
    if _INSTRUCTION.search(flat):
        return None, "that reads as an instruction to the assistant, not a fact about the household"
    if _MARKUP.search(flat):
        return None, "a fact is plain words: no markup, code or links"
    if flat[-1] not in ".!?":
        flat += "."
    return flat[0].upper() + flat[1:], ""


# Words that appear in every sentence carry no evidence of provenance.
_COMMON = frozenset("""the and for are was were has have had with that this these those his her its
    not but you she him they them their our your who what when where how will would can could
    also just very too now then than from into about over some any all is am be to of in on at
    it an as by or do my me we up so if no
    dont doesnt does did didnt isnt arent wasnt cant wont never longer anymore more""".split())
# Fraction of a fact's distinctive words that must be in the person's own
# message for the save to count as theirs. Half, not all: the model reworks
# the sentence ("I'm vegetarian by the way" -> "Manikanta is vegetarian"), and
# demanding a full match would put a question in front of every ordinary save.
REQUIRED_OVERLAP = 0.5


def _stem(word):
    """Enough to see that "likes", "liked" and "liking" are "like"; no more."""
    if word in ("goes", "does"):
        return word[:2]
    if word.endswith("ies") and len(word) > 4:
        word = word[:-3] + "y"
    elif word.endswith(("sses", "shes", "ches", "xes")):
        word = word[:-2]
    elif word.endswith("ing") and len(word) > 5:
        word = word[:-3]
    elif word.endswith("ed") and len(word) > 4:
        word = word[:-2]
    elif word.endswith("s") and not word.endswith("ss") and len(word) > 3:
        word = word[:-1]
    return word[:-1] if word.endswith("e") and len(word) > 3 else word


def _provenance_tokens(text):
    flat = re.sub("[’']", "", (text or "").lower())
    words = re.findall("[a-z0-9ఀ-౿ऀ-ॿ]+", flat)
    return {_stem(w) for w in words if w.isdigit() or (len(w) >= 2 and w not in _COMMON)}


def traces_to(fact_text, said, *, names=()):
    """Whether a fact's words came from what the person said this turn.

    `names` are words the model may add on its own without that counting
    against the fact: who is speaking (their user name and any name already in
    memory), since the person says "I" and the fact has to say who.
    """
    allowed = set()
    for name in names:
        allowed |= _provenance_tokens(name)
    claim = _provenance_tokens(fact_text) - allowed
    if not claim:
        return True
    heard = _provenance_tokens(said)
    return len(claim & heard) / len(claim) >= REQUIRED_OVERLAP


# ── what a person told, as against what they asked for ───────────────────────
# Owner's report (2026-10-16): memory filled with requests restated as facts:
# "Admin wants to know how many alerts ...", "Admin wants the lamp off after
# one minute from now", "Admin wants the lamp on when their device is home"
# (a rule the house already had as an automation). The distiller was told not
# to keep requests and questions and kept them anyway, and every one traced to
# the person's words, because a request is made of the person's words. So the
# words a fact may trace to are now only the sentences that tell something.

_FILLER_START = re.compile(r"^(?:(?:ok(?:ay)?|so|and|but|also|hey|hi|hello|narada|yes|yeah|no|well|"
                           r"um+|uh+|hmm+|right|alright)\b[\s,.!-]*)+", re.I)
_QUESTION_START = re.compile(r"^(what|whats|how|why|which|who|whom|whose|where|is|are|am|was|were|"
                             r"can|could|do|does|did|will|would|should|shall|may|has|have)\b", re.I)
_REQUEST_START = re.compile(
    r"^(please|kindly|(can|could|would|will) you|i (want|need|would like|d like) you to|let me know|"
    r"turn|switch|set|schedule|toggle|run|create|make|add|remove|delete|check|search|find|look|show|"
    r"tell|give|list|open|close|start|stop|remind|send|play|put|dim|lock|unlock|enable|disable|"
    r"cancel|help|explain|summari[sz]e|describe|analy[sz]e|update|change|rename|move|book|call)\b",
    re.I)
_FIRST_PERSON = re.compile(r"^(i|i'm|im|i've|ive|i'd|my|we|we're|were|our|me)\b", re.I)
_ASKS_TO_KEEP = re.compile(r"\b(remember|note (that|this|it|down)|keep (that|this|it)? ?in mind|"
                           r"don.t forget|make a note|save (that|this))\b", re.I)


def statements(said, *, acted=False):
    """The sentences of what someone said that tell something: not a question,
    not an order. "Dinner for four? My sister is allergic to peanuts." keeps
    the second sentence only. `acted`: Narada did something or looked
    something up in that turn, so the message was a request; then only what
    the person said about themselves ("I ...", "my ...", "we ...") is kept.
    Asking to be remembered ("remember that ...") always counts as telling."""
    kept = []
    for sentence in re.split(r"(?<=[.!?;])\s+|\n+", str(said or "")):
        sentence = sentence.strip()
        plain = _FILLER_START.sub("", sentence).strip().replace("’", "'")
        if not plain:
            continue
        if _ASKS_TO_KEEP.search(plain):
            kept.append(sentence)
            continue
        if plain.endswith("?") or _QUESTION_START.match(plain) or _REQUEST_START.match(plain):
            continue
        # "When my phone is detected, turn on the lamp": the order follows a
        # clause. "I sleep at 10, so switch the lights off" tells first.
        if not _FIRST_PERSON.match(plain) and any(
                _REQUEST_START.match(_FILLER_START.sub("", c).strip())
                for c in re.split(r",|\b(?:then|so)\b", plain)[1:]):
            continue
        if acted and not _FIRST_PERSON.match(plain):
            continue
        kept.append(sentence)
    return " ".join(kept)


def asks(said):
    """The sentences that ask for something to be done (not questions, not
    telling). Used only for a turn in which nothing was done: whether a
    lasting wish hides in one ("I want the lamp on when I get home") is not
    clear, so a fact traced to one is put to the person, not kept or lost."""
    told = statements(said)
    out = []
    for sentence in re.split(r"(?<=[.!?;])\s+|\n+", str(said or "")):
        sentence = sentence.strip()
        plain = _FILLER_START.sub("", sentence).strip()
        if plain and sentence not in told and not plain.endswith("?") and not _QUESTION_START.match(plain):
            out.append(sentence)
    return " ".join(out)


# A fact about one moment, not a lasting one: a timer, a question, today.
_ONE_OFF = re.compile(
    r"\bfrom now\b|\bright now\b|\b(at|for) the moment\b|"
    r"\bin (a|an|one|two|three|four|five|ten|fifteen|twenty|thirty|few|a few|couple of|\d+) "
    r"(seconds?|secs?|minutes?|mins?|hours?|hrs?)\b|"
    r"\b(today|tonight|tomorrow|yesterday|this (morning|afternoon|evening|week|weekend))\b|"
    r"\bwants? to (know|check|see|find out|learn|ask|look at|hear|be told)\b|"
    r"\b(asked|asks|is asking|was asking|wanted to know)\b|\bhow (many|much|often)\b",
    re.I)


def one_off(fact_text):
    """Why a fact is about one moment and not worth keeping, or "" when it is not."""
    match = _ONE_OFF.search(fact_text or "")
    return f'"{match.group(0)}" is about one moment, not something lasting' if match else ""
