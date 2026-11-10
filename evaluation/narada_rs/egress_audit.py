"""E7: what leaves the house when a rule is authored, over the whole path.

The compile request (nim_client) carries the schema and nothing live; that is
tested in tests/garuda_auto/test_nim_client.py. But a person authors a rule by
talking to Narada, and Narada's own turn goes to the same cloud first. This
drives the real agent through the 30 corpus requests, with canary readings in
the descriptor and the lamp switched on, and records every outbound request
body: which call it was, its size, and which live values it carried.

    DRISHTI_ENV_FILE=/path/.env python3 -m evaluation.narada_rs.egress_audit

No GPIO pin is opened (the relay bank's no-op mode), the house is a temporary
directory, and nothing is confirmed, so no rule is stored.
"""
import argparse
import json
import os
import re
import sys
import tempfile
import time

import requests
from dotenv import load_dotenv

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))
load_dotenv(os.environ.get("DRISHTI_ENV_FILE") or os.path.join(ROOT, ".env"))

import narada_eval                                                       # noqa: E402
from basic_pipelines.garuda_auto import nim_client                      # noqa: E402
from basic_pipelines.garuda_auto.llm import NimChat, parse_models       # noqa: E402
from evaluation.narada_rs.corpus import CORPUS                           # noqa: E402
from evaluation.narada_rs.run_eval import _dist, _write                  # noqa: E402

# Readings no schema, prompt or utterance would contain by chance.
CANARIES = {"person_count": 7, "occupancy_duration_s": 4127, "temperature_c": 27.83,
            "humidity_pct": 61.7, "ambient_luma": 143}
LIVE = {**CANARIES, "occupancy": "occupied", "zone": "desk", "posture": "seated", "hour": 21}


def _carried(text):
    """Which live values a request body carries."""
    found = [k for k, v in CANARIES.items() if re.search(rf"(?<![\d.]){re.escape(str(v))}(?![\d.])", text)]
    if re.search(r'\\?"id\\?":\s*\\?"lamp\\?"[^}]*\\?"state\\?":\s*\\?"on', text):
        found.append("device_state")
    if re.search(r'\\?"occupancy\\?":\s*\\?"occupied', text):
        found.append("occupancy")
    return found


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="only the first N requests (smoke test)")
    ap.add_argument("--tag", default="")
    args = ap.parse_args()
    sent = []

    def capture(url, json=None, **kw):
        sent.append({"body": json, "at": time.time()})
        return requests.post(url, json=json, **kw)

    chat = NimChat(os.environ.get("NIM_API_KEY", ""),
                   parse_models(os.environ.get("NIM_MODEL", ""), os.environ.get("NIM_FALLBACK_MODELS", "")),
                   post=capture)
    if not chat.configured:
        sys.exit("NIM_API_KEY is not set")

    rows = []
    for entry in CORPUS[:args.limit or None]:
        with tempfile.TemporaryDirectory(prefix="egress-audit-") as data_dir:
            house = narada_eval.House(data_dir, chat)
            try:
                house.ctx.nim.chat = chat           # as Garuda_web wires it
                house.ctx.descriptor.update(LIVE)
                house.home.set("lamp", "on", source="manual", actor="mani")
                start = len(sent)
                t0 = time.perf_counter()
                result = house.agent.handle(entry["utterance"], user="mani", role="admin", scope="home")
                elapsed = time.perf_counter() - t0
                proposals = house.ctx.pending.all()
            finally:
                house.close()
        calls = []
        for item in sent[start:]:
            body = item["body"] or {}
            text = json.dumps(body)
            compile_call = body.get("messages", [{}])[0].get("content") == nim_client._SYSTEM_PROMPT
            calls.append({"kind": "compile" if compile_call else "agent", "bytes": len(text),
                          "carried": _carried(text)})
        rows.append({"id": entry["id"], "utterance": entry["utterance"], "seconds": elapsed,
                     "reply": result.get("reply", ""), "actions": list(result.get("actions") or []),
                     "lane": result.get("lane"), "proposal_made": bool(proposals), "calls": calls})
        print(entry["id"], f"{elapsed:.1f}s", result.get("lane"), "proposal" if proposals else "-",
              [(c["kind"], c["carried"]) for c in calls], flush=True)

    flat = [c for r in rows for c in r["calls"]]
    summary = {}
    for kind in ("agent", "compile"):
        group = [c for c in flat if c["kind"] == kind]
        carried = {}
        for c in group:
            for k in c["carried"]:
                carried[k] = carried.get(k, 0) + 1
        summary[kind] = {"requests": len(group), "carrying_any_live_value": sum(1 for c in group if c["carried"]),
                         "carried_counts": carried, "bytes": _dist([c["bytes"] for c in group])}
    summary["turns"] = len(rows)
    summary["turns_with_proposal"] = sum(1 for r in rows if r["proposal_made"])
    summary["authoring_seconds"] = _dist([r["seconds"] for r in rows])
    summary["models"] = chat.models
    print(json.dumps(summary, indent=1))
    _write(f"egress_audit{args.tag}.json", {"live_values": LIVE, "summary": summary, "turns": rows})


if __name__ == "__main__":
    main()
