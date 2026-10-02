"""What a request is carrying, measured in tokens.

Counting exactly would mean shipping each model's tokenizer. This uses a
characters-per-token ratio per kind of text instead: prose is about four
characters a token, JSON is denser because braces, quotes and identifiers
fragment. It reads a little high on plain English, which is the right
direction for a budget: over-estimating wastes some window, under-estimating
overflows the request.
"""
import json

PROSE, MIXED, STRUCTURED = 4.0, 3.5, 3.0
PER_MESSAGE_OVERHEAD = 6      # role markers and separators around each message

# What a turn may carry of the conversation so far. The models behind Narada
# take far more; the bound is on latency and cost per turn, not on the window.
HISTORY_TOKENS = 3000


def tokens(text, density=PROSE):
    return int(len(text or "") / density) + 1


def message_tokens(message):
    """One chat message, including any tool calls it carries."""
    cost = PER_MESSAGE_OVERHEAD
    role = message.get("role")
    cost += tokens(message.get("content") or "", STRUCTURED if role == "tool" else PROSE)
    if message.get("tool_calls"):
        cost += tokens(json.dumps(message["tool_calls"], default=str), STRUCTURED)
    return cost


def turn_tokens(turn):
    return sum(message_tokens(m) for m in turn)


def fit_turns(turns, budget=HISTORY_TOKENS):
    """The most recent whole turns that fit, oldest first, and how many were left out.

    Newest first so a follow-up ("now turn it off") keeps the turn it refers
    to. A turn is carried whole or not at all: a question without its answer,
    or a tool call without its result, is worse than a stated omission -- and
    a dangling tool call is a request the API rejects.
    """
    kept, used = [], 0
    for turn in reversed(turns):
        cost = turn_tokens(turn)
        if used + cost > budget and kept:
            break
        if cost > budget and not kept:
            break                      # one turn larger than the whole budget: carry none
        kept.append(turn)
        used += cost
    kept.reverse()
    return kept, len(turns) - len(kept)
