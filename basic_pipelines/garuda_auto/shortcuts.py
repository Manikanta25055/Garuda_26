"""Shortcuts: small programs made of the things Narada can do.

A shortcut is data, never code: a trigger, an optional condition and a list of
steps. Each step is one capability (capabilities.py) with its arguments, or a
piece of control flow (wait, if, repeat, notify, run another shortcut, stop).
Nothing here is prebuilt; the model writes a shortcut when someone asks for
one, this module checks every part of it against what the house really has,
and a person confirms it before it is saved.

    {"name": "Movie night",
     "trigger": {"type": "manual"},
     "steps": [{"do": "run_scene", "args": {"scene": "movie"}},
               {"wait": 5400},
               {"if": {"field": "occupancy", "op": "==", "value": "empty"},
                "then": [{"do": "all_off", "args": {}}]}]}

Triggers
    manual                      run from a button or by asking
    time   at HH:MM [days]      on the clock; days are 0 (Monday) to 6
    every  minutes N            on an interval
    when   <condition> [for_minutes N]
                                the moment the condition becomes true (and has
                                stayed true for N minutes); not again until it
                                has been false in between

Conditions are over facts: the room as the camera sees it, device states,
modes, presence, alerts, and the clock ("time" as HH:MM, "weekday", "hour").
    {"field": f, "op": "==|!=|<|<=|>|>=", "value": v}
    {"all": [...]}  {"any": [...]}  {"not": {...}}
    {"between": ["22:00", "06:00"]}        a window on the clock, may cross midnight

A shortcut runs as the person who made it, with the role they have at that
moment; run by hand it runs as whoever pressed the button. Capabilities that
need a person's tap (the confirm tier) cannot be steps.
"""
import json
import logging
import re
import secrets
import threading
import time

from . import capabilities, jsonfile

log = logging.getLogger(__name__)

MAX_SHORTCUTS = 60
MAX_STEPS = 40              # counting nested ones
MAX_DEPTH = 4
MAX_WAIT_S = 6 * 3600
MAX_REPEAT = 50
MAX_RUNNING = 4
MAX_RUNS_KEPT = 30
MIN_COOLDOWN_S = 5
OPS = {"==": lambda a, b: a == b, "!=": lambda a, b: a != b,
       "<": lambda a, b: a < b, "<=": lambda a, b: a <= b,
       ">": lambda a, b: a > b, ">=": lambda a, b: a >= b}
CLOCK_FIELDS = ("time", "weekday", "hour", "minute")
_HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
_PLACEHOLDER = re.compile(r"\{([A-Za-z0-9_]+)\}")
# Not steps: these build or remove shortcuts and memories, or call a model.
NOT_STEPS = frozenset({"create_shortcut", "update_shortcut", "delete_shortcut", "check_shortcut",
                       "run_shortcut", "remember_fact", "forget_fact", "create_automation",
                       "hand_to_planner", "show_artifact", "take_snapshot"})
STEP_KINDS = ("do", "wait", "if", "repeat", "notify", "run", "stop")


def clock_facts(now):
    lt = time.localtime(now)
    return {"time": f"{lt.tm_hour:02d}:{lt.tm_min:02d}", "weekday": lt.tm_wday,
            "hour": lt.tm_hour, "minute": lt.tm_min}


# ── checking ──────────────────────────────────────────────────────────────────

class Invalid(ValueError):
    pass


def _check_condition(cond, fields, depth=0):
    if not isinstance(cond, dict) or len(cond) == 0:
        raise Invalid(f"a condition must be an object: {cond!r}")
    if depth > MAX_DEPTH:
        raise Invalid("conditions are nested too deeply")
    if "all" in cond or "any" in cond:
        key = "all" if "all" in cond else "any"
        if set(cond) != {key} or not isinstance(cond[key], list) or not cond[key]:
            raise Invalid(f"{key} takes a non-empty list of conditions and nothing else")
        for inner in cond[key]:
            _check_condition(inner, fields, depth + 1)
    elif "not" in cond:
        if set(cond) != {"not"}:
            raise Invalid("not takes one condition and nothing else")
        _check_condition(cond["not"], fields, depth + 1)
    elif "between" in cond:
        window = cond["between"]
        if set(cond) != {"between"} or not (isinstance(window, list) and len(window) == 2
                                            and all(isinstance(t, str) and _HHMM.match(t)
                                                    for t in window)):
            raise Invalid('between takes two clock times: ["HH:MM", "HH:MM"]')
    else:
        if set(cond) != {"field", "op", "value"}:
            raise Invalid(f"a comparison has exactly field, op and value: {cond!r}")
        if cond["field"] not in fields:
            raise Invalid(f"unknown field {cond['field']!r}; the fields are {sorted(fields)}")
        if cond["op"] not in OPS:
            raise Invalid(f"unknown operator {cond['op']!r}")
        if isinstance(cond["value"], (dict, list)) or cond["value"] is None:
            raise Invalid("a comparison's value is text, a number or true/false")


def _check_steps(steps, role, known, depth, count):
    if not isinstance(steps, list) or not steps:
        raise Invalid("steps must be a non-empty list")
    if depth > MAX_DEPTH:
        raise Invalid("steps are nested too deeply")
    for step in steps:
        if not isinstance(step, dict):
            raise Invalid(f"a step must be an object: {step!r}")
        kinds = [k for k in STEP_KINDS if k in step]
        if len(kinds) != 1:
            raise Invalid(f"a step is exactly one of {', '.join(STEP_KINDS)}: {step!r}")
        kind = kinds[0]
        count[0] += 1
        if count[0] > MAX_STEPS:
            raise Invalid(f"at most {MAX_STEPS} steps")
        if kind == "do":
            capability = capabilities.BY_NAME.get(step["do"])
            if capability is None:
                raise Invalid(f"there is no capability {step['do']!r}")
            if capability.tier == "confirm":
                raise Invalid(f"{capability.name} needs a person's tap each time, so it cannot "
                              "be a step")
            if capability.name in NOT_STEPS:
                raise Invalid(f"{capability.name} cannot be a step")
            if capability.role == "admin" and role != "admin":
                raise Invalid(f"only an admin's shortcut may use {capability.name}")
            args = step.get("args", {})
            if not isinstance(args, dict):
                raise Invalid(f"args of {capability.name} must be an object")
            extra = set(args) - set(capability.params)
            if extra:
                raise Invalid(f"{capability.name} takes no {sorted(extra)}")
            missing = [r for r in capability.required if r not in args]
            if missing:
                raise Invalid(f"{capability.name} needs {missing}")
            if set(step) - {"do", "args", "optional"}:
                raise Invalid(f"a do step has only do, args and optional: {step!r}")
        elif kind == "wait":
            seconds = step["wait"]
            if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) \
                    or not 0 < seconds <= MAX_WAIT_S:
                raise Invalid(f"wait is a number of seconds up to {MAX_WAIT_S}")
        elif kind == "if":
            _check_condition(step["if"], known["fields"])
            _check_steps(step.get("then"), role, known, depth + 1, count)
            if "else" in step:
                _check_steps(step["else"], role, known, depth + 1, count)
            if set(step) - {"if", "then", "else"}:
                raise Invalid("an if step has only if, then and else")
        elif kind == "repeat":
            times = step["repeat"]
            if isinstance(times, bool) or not isinstance(times, int) or not 1 <= times <= MAX_REPEAT:
                raise Invalid(f"repeat is a whole number from 1 to {MAX_REPEAT}")
            _check_steps(step.get("steps"), role, known, depth + 1, count)
        elif kind == "notify":
            if not isinstance(step["notify"], str) or not 0 < len(step["notify"]) <= 300:
                raise Invalid("notify is a line of text, at most 300 characters")
            if not isinstance(step.get("email", False), bool):
                raise Invalid("email is true or false")
        elif kind == "run":
            if step["run"] not in known["shortcuts"]:
                raise Invalid(f"there is no shortcut {step['run']!r}")
            if step["run"] == known.get("self"):
                raise Invalid("a shortcut cannot run itself")


def validate(program, *, role, fields, shortcut_ids=(), self_id=None):
    """Return the cleaned program, or raise Invalid with the reason."""
    if not isinstance(program, dict):
        raise Invalid("a shortcut is an object")
    extra = set(program) - {"name", "description", "trigger", "conditions", "steps", "cooldown_s"}
    if extra:
        raise Invalid(f"unknown keys: {sorted(extra)}")
    name = program.get("name")
    if not isinstance(name, str) or not 0 < len(name.strip()) <= 60:
        raise Invalid("name is needed, at most 60 characters")
    description = program.get("description", "")
    if not isinstance(description, str) or len(description) > 300:
        raise Invalid("description is text, at most 300 characters")
    fields = set(fields) | set(CLOCK_FIELDS)
    trigger = program.get("trigger") or {"type": "manual"}
    if not isinstance(trigger, dict):
        raise Invalid("trigger must be an object")
    kind = trigger.get("type")
    if kind == "manual":
        trigger = {"type": "manual"}
    elif kind == "time":
        days = trigger.get("days", [0, 1, 2, 3, 4, 5, 6])
        if not isinstance(trigger.get("at"), str) or not _HHMM.match(trigger["at"]):
            raise Invalid("a time trigger needs at: HH:MM (24 hour)")
        if not (isinstance(days, list) and days and all(
                isinstance(d, int) and not isinstance(d, bool) and 0 <= d <= 6 for d in days)):
            raise Invalid("days are numbers 0 (Monday) to 6 (Sunday)")
        trigger = {"type": "time", "at": trigger["at"], "days": sorted(set(days))}
    elif kind == "every":
        minutes = trigger.get("minutes")
        if isinstance(minutes, bool) or not isinstance(minutes, int) or not 1 <= minutes <= 1440:
            raise Invalid("an every trigger needs minutes: 1 to 1440")
        trigger = {"type": "every", "minutes": minutes}
    elif kind == "when":
        _check_condition(trigger.get("condition"), fields)
        held = trigger.get("for_minutes", 0)
        if isinstance(held, bool) or not isinstance(held, (int, float)) or not 0 <= held <= 720:
            raise Invalid("for_minutes is 0 to 720")
        trigger = {"type": "when", "condition": trigger["condition"], "for_minutes": held}
    else:
        raise Invalid("trigger type is one of manual, time, every, when")
    if trigger["type"] != "manual" and role != "admin":
        raise Invalid("only an admin can make a shortcut that runs by itself; a manual one is "
                      "allowed")
    if program.get("conditions") is not None:
        _check_condition(program["conditions"], fields)
    known = {"fields": fields, "shortcuts": set(shortcut_ids), "self": self_id}
    _check_steps(program.get("steps"), role, known, 0, [0])
    cooldown = program.get("cooldown_s", 60)
    if isinstance(cooldown, bool) or not isinstance(cooldown, (int, float)) \
            or not MIN_COOLDOWN_S <= cooldown <= 86400:
        raise Invalid(f"cooldown_s is {MIN_COOLDOWN_S} to 86400")
    clean = {"name": name.strip(), "description": description.strip(), "trigger": trigger,
             "steps": program["steps"], "cooldown_s": cooldown}
    if program.get("conditions") is not None:
        clean["conditions"] = program["conditions"]
    return clean


# ── evaluating ────────────────────────────────────────────────────────────────

def holds(cond, facts):
    if "all" in cond:
        return all(holds(c, facts) for c in cond["all"])
    if "any" in cond:
        return any(holds(c, facts) for c in cond["any"])
    if "not" in cond:
        return not holds(cond["not"], facts)
    if "between" in cond:
        start, end = cond["between"]
        now = facts.get("time", "")
        return start <= now < end if start <= end else (now >= start or now < end)
    actual, expected = facts.get(cond["field"]), cond["value"]
    if actual is None:
        return False
    if isinstance(expected, bool) or isinstance(actual, bool):
        actual, expected = _truth(actual), _truth(expected)
    elif isinstance(expected, (int, float)) != isinstance(actual, (int, float)):
        return False
    try:
        return OPS[cond["op"]](actual, expected)
    except TypeError:
        return False


def _truth(value):
    return value if isinstance(value, bool) else str(value).lower() in ("on", "true", "yes", "1")


# ── saying it in words ────────────────────────────────────────────────────────

_DAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def _say_condition(cond):
    if "all" in cond:
        return " and ".join(_say_condition(c) for c in cond["all"])
    if "any" in cond:
        return "(" + " or ".join(_say_condition(c) for c in cond["any"]) + ")"
    if "not" in cond:
        return f"not ({_say_condition(cond['not'])})"
    if "between" in cond:
        return f"between {cond['between'][0]} and {cond['between'][1]}"
    op = {"==": "is", "!=": "is not"}.get(cond["op"], cond["op"])
    return f"{cond['field'].replace('_', ' ')} {op} {cond['value']}"


def _say_trigger(trigger):
    kind = trigger["type"]
    if kind == "manual":
        return "When you run it"
    if kind == "time":
        days = trigger["days"]
        which = ("every day" if len(days) == 7 else "on weekdays" if days == [0, 1, 2, 3, 4]
                 else "at weekends" if days == [5, 6] else "on " + ", ".join(_DAYS[d] for d in days))
        return f"At {trigger['at']} {which}"
    if kind == "every":
        return f"Every {trigger['minutes']} min"
    held = trigger.get("for_minutes")
    return f"When {_say_condition(trigger['condition'])}" + (f" for {held:g} min" if held else "")


def _say_steps(steps, names, depth=0):
    lines = []
    for step in steps:
        pad = "  " * depth
        if "do" in step:
            args = ", ".join(f"{k} {v}" for k, v in (step.get("args") or {}).items())
            lines.append(f"{pad}{step['do'].replace('_', ' ').capitalize()}"
                         + (f": {args}" if args else ""))
        elif "wait" in step:
            seconds = step["wait"]
            lines.append(f"{pad}Wait " + (f"{seconds / 60:g} min" if seconds >= 60
                                         else f"{seconds:g} s"))
        elif "if" in step:
            lines.append(f"{pad}If {_say_condition(step['if'])}:")
            lines += _say_steps(step["then"], names, depth + 1)
            if step.get("else"):
                lines.append(f"{pad}Otherwise:")
                lines += _say_steps(step["else"], names, depth + 1)
        elif "repeat" in step:
            lines.append(f"{pad}Repeat {step['repeat']} times:")
            lines += _say_steps(step["steps"], names, depth + 1)
        elif "notify" in step:
            lines.append(f"{pad}Notify{' by email' if step.get('email') else ''}: {step['notify']}")
        elif "run" in step:
            lines.append(f"{pad}Run shortcut {names.get(step['run'], step['run'])}")
        elif "stop" in step:
            lines.append(f"{pad}Stop")
    return lines


# Modes that stop the security system from telling anyone what it sees.
SILENCING_MODES = {"idle": "all alerts off", "email_off": "alert emails off", "dnd": "alerts silenced"}


def cautions(program):
    """What a person should notice before agreeing to this shortcut."""
    out = []
    steps = list(_walk(program.get("steps") or []))
    for step in steps:
        args = step.get("args") or {}
        if step.get("do") == "set_security_mode" and args.get("on") is True \
                and args.get("mode") in SILENCING_MODES:
            line = f"Turns on {args['mode']} mode: {SILENCING_MODES[args['mode']]} while it is on."
            if line not in out:
                out.append(line)
    trigger = program.get("trigger") or {}
    if trigger.get("type") == "every" and trigger.get("minutes", 60) < 5:
        out.append(f"Runs every {trigger['minutes']} min, day and night.")
    if any(s.get("notify") and s.get("email") for s in steps) and trigger.get("type") in ("every", "when"):
        out.append("Sends email each time it runs.")
    return out


def _walk(steps):
    for step in steps:
        yield step
        for key in ("then", "else", "steps"):
            if isinstance(step.get(key), list):
                yield from _walk(step[key])


def describe(program, names=None):
    """The shortcut in plain lines, for the card a person confirms and for the page."""
    out = {"when": _say_trigger(program.get("trigger") or {"type": "manual"}),
           "steps": _say_steps(program.get("steps") or [], names or {})}
    if program.get("conditions"):
        out["only_if"] = _say_condition(program["conditions"])
    return out


# ── keeping ───────────────────────────────────────────────────────────────────

class ShortcutStore:
    def __init__(self, path, clock=time.time):
        self._path = path
        self._clock = clock
        self._lock = threading.RLock()
        self.items = jsonfile.load(path, []) if path else []

    def _save(self):
        if self._path:
            jsonfile.save(self._path, self.items)

    def all(self):
        with self._lock:
            return [dict(s) for s in self.items]

    def get(self, shortcut_id):
        with self._lock:
            return next((dict(s) for s in self.items if s["id"] == shortcut_id), None)

    def find(self, words):
        """By id, or by name, for a person who says "run movie night"."""
        words = str(words or "").strip().lower()
        with self._lock:
            for s in self.items:
                if s["id"] == words or s["name"].lower() == words:
                    return dict(s)
            return next((dict(s) for s in self.items if words and words in s["name"].lower()), None)

    def add(self, program, created_by):
        with self._lock:
            if len(self.items) >= MAX_SHORTCUTS:
                raise Invalid(f"at most {MAX_SHORTCUTS} shortcuts")
            if any(s["name"].lower() == program["name"].lower() for s in self.items):
                raise Invalid(f"there is already a shortcut called {program['name']}")
            entry = {"id": secrets.token_hex(4), **program, "enabled": True,
                     "created_by": created_by, "created": self._clock(), "last_run": None,
                     "runs": []}
            self.items.append(entry)
            self._save()
            return dict(entry)

    def replace(self, shortcut_id, program):
        with self._lock:
            for s in self.items:
                if s["id"] == shortcut_id:
                    for key in ("conditions",):
                        s.pop(key, None)
                    s.update(program)
                    self._save()
                    return dict(s)
        return None

    def toggle(self, shortcut_id):
        with self._lock:
            for s in self.items:
                if s["id"] == shortcut_id:
                    s["enabled"] = not s.get("enabled", True)
                    self._save()
                    return s["enabled"]
        return None

    def delete(self, shortcut_id):
        with self._lock:
            before = len(self.items)
            self.items[:] = [s for s in self.items if s["id"] != shortcut_id]
            if len(self.items) != before:
                self._save()
                return True
        return False

    def record_run(self, shortcut_id, run):
        with self._lock:
            for s in self.items:
                if s["id"] == shortcut_id:
                    s["last_run"] = run["started"]
                    s["runs"] = ([run] + s.get("runs", []))[:MAX_RUNS_KEPT]
                    self._save()
                    return


# ── running ───────────────────────────────────────────────────────────────────

class _Stop(Exception):
    pass


def fill(text, facts):
    """`{field}` replaced by the fact's value; anything else is left as written.

    Not str.format: the text is the model's, and a format string can walk an
    object's attributes ("{time.__class__}").
    """
    return _PLACEHOLDER.sub(lambda m: str(facts[m.group(1)]) if m.group(1) in facts else m.group(0), text)


class ShortcutEngine:
    """Watches triggers once a second and runs shortcuts on worker threads.

    do_fn(name, args, user, role) -> dict   carries out one capability
    facts_fn() -> dict                      what is true of the house now
    role_of(user) -> "admin" | "user" | None
    notify_fn(text, email) -> None
    """

    def __init__(self, store, *, do_fn, facts_fn, role_of, notify_fn=None, on_change=None,
                 clock=time.time, sleep=None):
        self.store = store
        self.do_fn = do_fn
        self.facts_fn = facts_fn
        self.role_of = role_of
        self.notify_fn = notify_fn or (lambda text, email: None)
        self.on_change = on_change or (lambda: None)
        self._clock = clock
        self._stop = threading.Event()
        self._sleep = sleep or self._stop.wait
        self._lock = threading.Lock()
        self._running = {}          # shortcut id -> live progress
        self._true_since = {}       # when-trigger: since when its condition has held
        self._fired = {}            # when-trigger: already fired for this stretch
        self._last_auto = {}        # shortcut id -> time of its last automatic run
        self._last_minute = ""
        self._seen_when = set()     # when-triggers this run of the service has looked at
        self._thread = None
        self.last_error = ""

    def facts(self):
        try:
            facts = dict(self.facts_fn() or {})
        except Exception as exc:
            self.last_error = f"facts: {type(exc).__name__}: {exc}"
            facts = {}
        facts.update(clock_facts(self._clock()))
        return facts

    def fields(self):
        return set(self.facts())

    def check(self, program, role, self_id=None):
        return validate(program, role=role, fields=self.fields(),
                        shortcut_ids=[s["id"] for s in self.store.all()], self_id=self_id)

    def names(self):
        return {s["id"]: s["name"] for s in self.store.all()}

    # ── triggers ──────────────────────────────────────────────────────────────

    def tick(self):
        now = self._clock()
        facts = self.facts()
        minute = time.strftime("%Y-%m-%d %H:%M", time.localtime(now))
        new_minute, self._last_minute = minute != self._last_minute, minute
        for shortcut in self.store.all():
            sid, trigger = shortcut["id"], shortcut["trigger"]
            if not shortcut.get("enabled", True) or trigger["type"] == "manual":
                continue
            due = False
            if trigger["type"] == "time":
                due = (new_minute and facts["time"] == trigger["at"]
                       and facts["weekday"] in trigger["days"]
                       # Restarted inside its minute, having already run in it.
                       and not self._ran_this_minute(shortcut, now))
            elif trigger["type"] == "every":
                last = self._last_auto.get(sid)
                if last is None:
                    self._last_auto[sid] = now      # counted from start-up, not run at once
                else:
                    due = now - last >= trigger["minutes"] * 60
            elif trigger["type"] == "when":
                first_look = (sid, json.dumps(trigger, sort_keys=True)) not in self._seen_when
                self._seen_when.add((sid, json.dumps(trigger, sort_keys=True)))
                if first_look and shortcut.get("last_run") and holds(trigger["condition"], facts):
                    # The service has just started and the condition already
                    # holds for a shortcut that has run before: that stretch is
                    # not new. Without this every restart ran it again ("when I
                    # am away, turn everything off", each time the Pi restarted).
                    self._true_since[sid] = now
                    self._fired[sid] = True
                if holds(trigger["condition"], facts):
                    since = self._true_since.setdefault(sid, now)
                    if not self._fired.get(sid) and now - since >= trigger["for_minutes"] * 60:
                        due = self._fired[sid] = True
                else:
                    self._true_since.pop(sid, None)
                    self._fired.pop(sid, None)
            if not due:
                continue
            last = self._last_auto.get(sid)
            if trigger["type"] == "when" and last is not None \
                    and now - last < shortcut.get("cooldown_s", 60):
                continue
            if trigger["type"] == "every":
                self._last_auto[sid] = now      # the interval passes whether or not it runs
            if shortcut.get("conditions") and not holds(shortcut["conditions"], facts):
                continue
            self._last_auto[sid] = now
            self.run(sid, by=shortcut.get("created_by", ""), cause=trigger["type"])

    @staticmethod
    def _ran_this_minute(shortcut, now):
        last = shortcut.get("last_run")
        return bool(last) and time.strftime("%Y%m%d%H%M", time.localtime(last)) == \
            time.strftime("%Y%m%d%H%M", time.localtime(now))

    # ── one run ───────────────────────────────────────────────────────────────

    def run(self, shortcut_id, *, by, role=None, cause="manual", wait=False, depth=0):
        """Start a shortcut. Returns (ok, reason, run id)."""
        shortcut = self.store.get(shortcut_id)
        if shortcut is None:
            return False, "no such shortcut", None
        role = role or self.role_of(by)
        if role is None:
            return False, f"{by or 'its maker'} can no longer sign in", None
        with self._lock:
            if shortcut_id in self._running:
                return False, "it is already running", None
            if len(self._running) >= MAX_RUNNING and depth == 0:
                return False, "too many shortcuts are running", None
            progress = {"id": secrets.token_hex(4), "shortcut": shortcut_id,
                        "name": shortcut["name"], "by": by, "cause": cause,
                        "started": self._clock(), "steps": [], "now": "starting", "cancel": False}
            self._running[shortcut_id] = progress
        if wait:
            self._carry_out(shortcut, progress, by, role, depth)
        else:
            threading.Thread(target=self._carry_out, args=(shortcut, progress, by, role, depth),
                             daemon=True, name=f"shortcut-{shortcut_id}").start()
        return True, "", progress["id"]

    def cancel(self, shortcut_id):
        with self._lock:
            progress = self._running.get(shortcut_id)
            if progress is None:
                return False
            progress["cancel"] = True
            return True

    def running(self):
        with self._lock:
            return [{k: v for k, v in p.items() if k != "cancel"} | {"steps": list(p["steps"])}
                    for p in self._running.values()]

    def _carry_out(self, shortcut, progress, by, role, depth):
        outcome = "done"
        try:
            self._steps(shortcut["steps"], progress, by, role, depth)
        except _Stop as stop:
            outcome = str(stop) or "stopped"
        except Exception as exc:
            log.exception("shortcut %s", shortcut["id"])
            outcome = f"failed: {type(exc).__name__}: {exc}"
        with self._lock:
            self._running.pop(shortcut["id"], None)
        run = {"id": progress["id"], "started": progress["started"],
               "ended": self._clock(), "by": by, "cause": progress["cause"],
               "outcome": outcome, "steps": progress["steps"][-MAX_STEPS:]}
        self.store.record_run(shortcut["id"], run)
        if outcome.startswith("failed") and progress["cause"] != "manual":
            self.notify_fn(f"Shortcut {shortcut['name']} {outcome}", False)
        self.on_change()

    def _note(self, progress, text, ok=True, detail=""):
        progress["steps"].append({"at": self._clock(), "step": text, "ok": ok,
                                  "detail": str(detail)[:200]})
        progress["now"] = text
        self.on_change()

    def _steps(self, steps, progress, by, role, depth):
        for step in steps:
            if progress["cancel"] or self._stop.is_set():
                raise _Stop("cancelled")
            if "do" in step:
                out = self.do_fn(step["do"], dict(step.get("args") or {}), by, role)
                failed = isinstance(out, dict) and ("error" in out or out.get("ok") is False)
                detail = (out.get("error") or out.get("reason") or out.get("result") or ""
                          if isinstance(out, dict) else "")
                self._note(progress, step["do"], not failed, detail)
                if failed and not step.get("optional"):
                    raise _Stop(f"failed: {step['do']}: {detail or 'refused'}")
            elif "wait" in step:
                self._note(progress, f"wait {step['wait']:g}s")
                end = self._clock() + step["wait"]
                while self._clock() < end:
                    if progress["cancel"]:
                        raise _Stop("cancelled")
                    if self._sleep(min(1.0, max(0.0, end - self._clock()))):
                        raise _Stop("cancelled")
            elif "if" in step:
                branch = "then" if holds(step["if"], self.facts()) else "else"
                self._note(progress, f"if: {branch}")
                if step.get(branch):
                    self._steps(step[branch], progress, by, role, depth)
            elif "repeat" in step:
                for _ in range(step["repeat"]):
                    self._steps(step["steps"], progress, by, role, depth)
            elif "notify" in step:
                text = fill(step["notify"], self.facts())
                self.notify_fn(text, bool(step.get("email")))
                self._note(progress, "notify", detail=text)
            elif "run" in step:
                if depth >= MAX_DEPTH:
                    raise _Stop("failed: shortcuts are nested too deeply")
                ok, reason, _ = self.run(step["run"], by=by, role=role, cause="shortcut",
                                         wait=True, depth=depth + 1)
                self._note(progress, f"run {step['run']}", ok, reason)
                if not ok:
                    raise _Stop(f"failed: run {step['run']}: {reason}")
            elif "stop" in step:
                raise _Stop("stopped")

    # ── the loop ──────────────────────────────────────────────────────────────

    def start(self, interval=1.0):
        if self._thread is not None:
            return
        self._stop.clear()

        def loop():
            while not self._stop.wait(interval):
                try:
                    self.tick()
                except Exception as exc:
                    self.last_error = f"{type(exc).__name__}: {exc}"
                    log.exception("shortcut loop")

        self._thread = threading.Thread(target=loop, daemon=True, name="shortcuts")
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None
