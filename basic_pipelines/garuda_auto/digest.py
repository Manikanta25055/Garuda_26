"""A short account of what the house did today, and why.

Facts are counted locally from the actuation log and Garuda's alert history.
When NIM is configured the facts (counts, device names, rule sentences -- no
frames, no readings) are turned into a few friendly sentences; otherwise the
plain local version is the digest. Cached per day so opening the Insights
page repeatedly does not spend tokens.
"""
import json
import time

from .llm import NimUnavailable


def facts(home, alerts_today=0, now=None):
    now = time.time() if now is None else now
    lt = time.localtime(now)
    midnight = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1))
    entries = [e for e in home.log_entries() if e.get("ts", 0) >= midnight]
    names = {d["id"]: d["name"] for d in home.ctx.registry.devices}
    rules = {r.get("id"): r.get("source_utterance", "") for r in home.ctx.store.rules}
    by_source, failures, fired = {}, [], {}
    for e in entries:
        src = e.get("source") or ("rule" if e.get("rule_id") else "manual")
        kind = src.split(":")[0]
        by_source[kind] = by_source.get(kind, 0) + 1
        if not e.get("ok"):
            failures.append(f"{names.get(e['device'], e['device'])} {e['action']}: {e.get('reason', '')}")
        if e.get("rule_id"):
            fired[e["rule_id"]] = fired.get(e["rule_id"], 0) + 1
    use = home.usage(days=1)
    return {
        "date": time.strftime("%A %d %B", lt),
        "actions": len(entries),
        "by_source": by_source,
        "rules_fired": [{"rule": rules.get(rid, rid), "times": n} for rid, n in fired.items()],
        "failures": failures[:5],
        "on_hours": {r["name"]: r["hours"] for r in use["devices"] if r["hours"]},
        "energy_kwh": use["total_kwh"],
        "security_alerts": alerts_today,
        "owner_presence": home.context()["owner_presence"],
    }


def local_text(f):
    lines = [f"{f['date']}: {f['actions']} device actions."]
    if f["by_source"]:
        lines.append("By source: " + ", ".join(f"{k} {v}" for k, v in sorted(f["by_source"].items())) + ".")
    for r in f["rules_fired"][:5]:
        lines.append(f"Rule \"{r['rule']}\" ran {r['times']}x.")
    if f["on_hours"]:
        lines.append("On-time: " + ", ".join(f"{k} {v:.1f} h" for k, v in f["on_hours"].items()) + ".")
    if f["energy_kwh"]:
        lines.append(f"Estimated energy: {f['energy_kwh']:.2f} kWh.")
    lines.append(f"Security alerts: {f['security_alerts']}.")
    if f["failures"]:
        lines.append("Problems: " + "; ".join(f["failures"]) + ".")
    return "\n".join(lines)


class Digest:
    def __init__(self, home, chat=None, alerts_fn=None, clock=time.time):
        self.home = home
        self.chat = chat
        self.alerts_fn = alerts_fn or (lambda: 0)
        self._clock = clock
        self._cache = {}

    def build(self, refresh=False):
        now = self._clock()
        day = time.strftime("%Y-%m-%d", time.localtime(now))
        # Refreshed at most every 15 minutes even when asked, so a page that
        # polls cannot turn into a token meter.
        cached = self._cache.get(day)
        if cached and (not refresh or now - cached["generated_at"] < 900):
            return cached
        f = facts(self.home, alerts_today=self.alerts_fn(), now=now)
        text, source = local_text(f), "local"
        if self.chat is not None and self.chat.configured:
            try:
                message = self.chat.chat([
                    {"role": "system", "content":
                        "You write a daily home summary for the owner. 3-5 short sentences, "
                        "plain English, no markdown headings, no emojis. Mention anything that "
                        "failed or looks unusual first. Use only the facts given."},
                    {"role": "user", "content": json.dumps(f)},
                ], max_tokens=700, temperature=0.4, timeout=25)
                content = (message.get("content") or "").strip()
                if content:
                    text, source = content, "nim"
            except NimUnavailable:
                pass
        result = {"date": day, "text": text, "facts": f, "source": source,
                  "generated_at": now}
        self._cache = {day: result}
        return result

    def text(self):
        return self.build(refresh=True)["text"]
