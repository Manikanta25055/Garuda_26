"""Held-out requests written by people outside the project.

    python3 -m evaluation.narada_rs.heldout parse REPLIES.txt OUT.json

turns the pasted replies into entries with an unset reference. The authors
then write each reference rule (or null: the room cannot do this, so the
system should refuse) and set "frozen_at" to the commit that holds them,
BEFORE any model sees the requests. load() refuses an unfrozen file, so a
reference cannot be adjusted after the fact without it showing in git.

Reference, per entry, one of:
    {"combinator": "all" | "any", "when": [{field, op, value}, ...],
     "then": [{device, action}, ...], "vague": [field, ...]}
    null        the room cannot do this (a device or sense it lacks): refuse
    "compound"  needs two or more rules, e.g. (A and B) or (C and D): a rule
                has one combinator and no nesting, so it cannot be one rule
"""
import json
import re
import sys

UNSET = "unset"


def parse(text):
    entries, writer = [], None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("# "):
            continue
        head = re.match(r"^##\s*(\S+)", line)
        if head:
            writer = head.group(1)
            continue
        if writer is None:
            continue
        meant = None
        m = re.match(r"^(.*?)\s*\(([^()]*)\)\s*$", line)
        if m:
            line, meant = m.group(1).strip(), m.group(2).strip()
        line = re.sub(r"^\s*(\d+[.)]|[-*])\s*", "", line)
        entries.append({"id": f"h{len(entries) + 1:03d}", "writer": writer, "utterance": line,
                        "meant": meant, "reference": UNSET})
    return {"frozen_at": None, "entries": entries}


def load(path):
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    if not data.get("frozen_at"):
        sys.exit(f"{path}: references are not frozen (set frozen_at to the commit that holds them)")
    unset = [e["id"] for e in data["entries"] if e["reference"] == UNSET]
    if unset:
        sys.exit(f"{path}: references still unset for {', '.join(unset)}")
    return data["entries"]


def reference_rule(entry):
    """The entry's reference as a rule the scorer accepts, or None (should refuse)."""
    ref = entry["reference"]
    if ref is None or ref == "compound":
        return None
    return {"id": f"ref_{entry['id']}", "source_utterance": entry["utterance"],
            "when": {ref.get("combinator", "all"): [dict(c) for c in ref["when"]]},
            "then": [dict(a) for a in ref["then"]], "cooldown_s": 60, "enabled": True}


if __name__ == "__main__":
    if len(sys.argv) != 4 or sys.argv[1] != "parse":
        sys.exit(__doc__)
    with open(sys.argv[2], encoding="utf-8") as fh:
        out = parse(fh.read())
    with open(sys.argv[3], "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=1, ensure_ascii=False)
    print(f"{len(out['entries'])} requests from {len({e['writer'] for e in out['entries']})} writers")
