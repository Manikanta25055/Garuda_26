"""Narada as a home agent: one entry point for chat, voice and the composer.

Every request goes to NVIDIA NIM with the house's tools. The model reads
state, switches devices, runs scenes, sets timers, changes Garuda modes and
drafts automations, then says what it did. Nothing changes the house without
the model: when NIM is unconfigured or unreachable, Narada says so and does
nothing. (Until 2026-10 a local fast lane and keyword fallbacks acted without
the model; the owner wants one intelligent path for every action.)

Automations are never saved by the model. create_automation compiles the
sentence into a rule proposal; a person confirms it on the Automations page
(an admin, for a user's proposal). Recurring schedules and scenes are admin
actions; anyone signed in may switch devices and set a one-off timer, which is
the same power the dashboard buttons already give them.
"""
import json
import logging
import time
from collections import deque

from . import actuation_log
from .device_types import is_actuator
from .llm import NimUnavailable
from .rule_schema import render_rule

log = logging.getLogger(__name__)

MAX_ROUNDS = 5
HISTORY_TURNS = 6
MODE_NAMES = ("dnd", "night", "idle", "emergency", "privacy", "email_off")
# What Narada may do on the security-only product (Garuda). Home automation
# is Drishti's; on Garuda's address the model is not even offered it.
SECURITY_TOOLS = ("get_security_state", "set_security_mode")
# Spoken replies: every character is synthesised (and billed), lists and
# markdown read aloud badly, and a reply that sounds written feels robotic.
VOICE_STYLE = (
    "\nYou are speaking out loud in a live conversation. Sound like a warm, "
    "quick-witted person, not a report: use contractions and plain words, keep it "
    "to one or two short sentences (under 200 characters), and it's fine to end "
    "with a brief follow-up question when it helps. Never read out lists, markdown, "
    "symbols, ids or model names. If the person is just chatting, chat back.")
_DAY_SETS = {"daily": [0, 1, 2, 3, 4, 5, 6], "weekdays": [0, 1, 2, 3, 4],
             "weekends": [5, 6]}


def _fn(name, description, properties=None, required=()):
    return {"type": "function", "function": {
        "name": name, "description": description,
        "parameters": {"type": "object", "properties": properties or {},
                       "required": list(required)}}}


class HomeAgent:
    def __init__(self, ctx, home, chat, decision, *, modes_fn=None, set_mode_fn=None,
                 security_fn=None, clock=time.time):
        self.ctx = ctx
        self.home = home
        self.chat = chat
        self.decision = decision
        self.modes_fn = modes_fn or (lambda: {})
        self.set_mode_fn = set_mode_fn
        self.security_fn = security_fn or (lambda: {})
        self._clock = clock
        self._history = {}
        self.stats = {"agent": 0, "unavailable": 0}

    # ── entry point ───────────────────────────────────────────────────────────

    def handle(self, text, *, user="", role="user", scope="home", voice=False):
        text = (text or "").strip()[:500]
        if not text:
            return {"reply": "Say something for me to do.", "lane": "agent", "actions": []}
        route_view = None
        if scope != "security":
            devices = [{"id": d["id"], "name": d["name"], "room": d.get("room", "")}
                       for d in self.ctx.registry.devices if d.get("enabled", True)]
            scenes = [{"id": s["id"], "name": s["name"]} for s in self.home.scenes.scenes]
            route = self.decision.route(text, devices, scenes)
            route_view = {k: {"value": v["value"], "confidence": v["confidence"]}
                          for k, v in route.items()}
            route_view["backend"] = next(iter(route.values()))["backend"] if route else "local"
        if self.chat is None or not self.chat.configured:
            result = self._unavailable("the NVIDIA NIM key is not configured")
        else:
            try:
                result = self._agent(text, user, role, scope=scope, voice=voice)
            except NimUnavailable as exc:
                result = self._unavailable(str(exc))
        if route_view is not None:
            result["route"] = route_view
        self.stats[result["lane"]] = self.stats.get(result["lane"], 0) + 1
        if result["lane"] == "agent":
            self._remember(f"security:{user}" if scope == "security" else user,
                           text, result["reply"])
        return result

    @staticmethod
    def _unavailable(why):
        return {"reply": f"I can't act right now: the AI service is unavailable ({why}). "
                         "Nothing was changed.",
                "lane": "unavailable", "actions": []}

    # ── agent lane ────────────────────────────────────────────────────────────

    def _system_prompt(self, user, role):
        now = time.localtime(self._clock())
        return (
            "You are Narada, the assistant of Garuda, a home security and home automation "
            "system on a Raspberry Pi 5. You control the house only through the tools. "
            f"It is {time.strftime('%A %d %B %Y, %H:%M', now)} local time. "
            f"You are talking to {user or 'a resident'} (role: {role}).\n"
            "Rules:\n"
            "- Never invent devices, scenes or readings; call get_house_state when unsure.\n"
            "- A conditional instruction (when/if/whenever ...) is an automation: call "
            "create_automation with the person's sentence. It becomes a proposal they confirm.\n"
            "- A time-based instruction ('at 7 pm', 'in 20 minutes', 'every weekday') is a "
            "schedule: call schedule_action.\n"
            "- Only switch devices the person asked about. Confirm what you did in one or two "
            "short sentences. No emojis. If a tool refused, say why.\n"
            "- Security modes: dnd, night, idle, emergency, privacy, email_off.")

    def _tools(self):
        return [
            _fn("get_security_state", "Security modes, alerts, camera, occupancy and presence."),
            _fn("get_house_state", "Devices with state, scenes, occupancy, presence, security, modes."),
            _fn("set_device", "Switch one device on or off.",
                {"device": {"type": "string", "description": "device id"},
                 "action": {"type": "string", "enum": ["on", "off"]}}, ["device", "action"]),
            _fn("all_off", "Turn off every device, optionally only in one room.",
                {"room": {"type": "string"}}),
            _fn("run_scene", "Run a saved scene.", {"scene": {"type": "string"}}, ["scene"]),
            _fn("create_scene", "Save a new scene (admin only).",
                {"name": {"type": "string"},
                 "actions": {"type": "array", "items": {"type": "object", "properties": {
                     "device": {"type": "string"}, "action": {"type": "string"}}}}},
                ["name", "actions"]),
            _fn("schedule_action",
                "Schedule a device action or a scene. Give in_minutes for a timer, or time "
                "(HH:MM 24h) with repeat=true for a recurring schedule (admin only).",
                {"device": {"type": "string"}, "action": {"type": "string", "enum": ["on", "off"]},
                 "scene": {"type": "string"}, "in_minutes": {"type": "number"},
                 "time": {"type": "string"}, "repeat": {"type": "boolean"},
                 "days": {"type": "string", "enum": ["daily", "weekdays", "weekends"]}}),
            _fn("list_schedules", "List schedules and timers."),
            _fn("create_automation",
                "Draft a condition-based automation from the person's own sentence.",
                {"instruction": {"type": "string"}}, ["instruction"]),
            _fn("list_automations", "List saved automations (rules)."),
            _fn("set_security_mode", "Turn a Garuda security mode on or off.",
                {"mode": {"type": "string", "enum": list(MODE_NAMES)},
                 "on": {"type": "boolean"}}, ["mode", "on"]),
            _fn("recent_activity", "Recent device actions with who or what caused them.",
                {"limit": {"type": "integer"}}),
            _fn("energy_usage", "Device on-time and estimated energy.",
                {"days": {"type": "integer"}}),
        ]

    def _security_prompt(self, user, role):
        now = time.localtime(self._clock())
        return (
            "You are Narada, the assistant of Garuda, an AI home security system on a "
            "Raspberry Pi 5 with a Hailo accelerator and a camera that detects people and "
            "dangerous objects (knife, scissors, hammer) and emails alerts. "
            f"It is {time.strftime('%A %d %B %Y, %H:%M', now)} local time. "
            f"You are talking to {user or 'a resident'} (role: {role}).\n"
            "Use get_security_state for anything about the current situation and "
            "set_security_mode to change a mode (dnd, night, idle, emergency, privacy, "
            "email_off). You do not control lights or appliances here; if asked, say that "
            "home automation lives in the Drishti app. Be concise. No emojis.")

    def _agent(self, text, user, role, scope="home", voice=False):
        system = self._security_prompt(user, role) if scope == "security" else self._system_prompt(user, role)
        if voice:
            system += VOICE_STYLE
        tools = self._tools()
        if scope == "security":
            tools = [t for t in tools if t["function"]["name"] in SECURITY_TOOLS]
        messages = [{"role": "system", "content": system}]
        # Separate memories, so a Drishti conversation never leaks into Garuda's.
        history_key = f"security:{user}" if scope == "security" else user
        messages += list(self._history.get(history_key, ()))
        messages.append({"role": "user", "content": text})
        actions, proposal = [], None
        for _ in range(MAX_ROUNDS):
            message = self.chat.chat(messages, tools=tools, max_tokens=1200,
                                     temperature=0.2)
            calls = message.get("tool_calls") or []
            if not calls:
                reply = (message.get("content") or "").strip() or "Done."
                return {"reply": reply, "lane": "agent", "actions": actions,
                        "proposal": proposal, "model": self.chat.last_model}
            messages.append({"role": "assistant", "content": message.get("content") or "",
                             "tool_calls": calls})
            for call in calls:
                fn = call.get("function", {})
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except ValueError:
                    args = {}
                name = fn.get("name", "")
                if scope == "security" and name not in SECURITY_TOOLS:
                    out = {"error": f"{name} is not available in Garuda"}
                else:
                    out = self._run_tool(name, args if isinstance(args, dict) else {}, user, role)
                if out.pop("_action", None):
                    actions.append(out.get("result", ""))
                if out.get("proposal"):
                    proposal = out["proposal"]
                messages.append({"role": "tool", "tool_call_id": call.get("id", ""),
                                 "content": json.dumps(out)[:4000]})
        return {"reply": "I did what I could: " + "; ".join(actions) if actions
                else "That took too many steps; try asking more specifically.",
                "lane": "agent", "actions": actions, "proposal": proposal,
                "model": self.chat.last_model}

    # ── tools ─────────────────────────────────────────────────────────────────

    def _run_tool(self, name, args, user, role):
        try:
            handler = getattr(self, f"_tool_{name}")
        except AttributeError:
            return {"error": f"unknown tool {name}"}
        try:
            return handler(args, user, role)
        except Exception as exc:
            log.exception("tool %s", name)
            return {"error": f"{type(exc).__name__}: {exc}"}

    def state_snapshot(self):
        d = self.ctx.descriptor
        ctx = self.home.context()
        snapshot = {
            "devices": [{"id": x["id"], "name": x["name"], "room": x.get("room"),
                         "type": x["type"], "state": x["state"], "available": x["available"]}
                        for x in self.home.devices()],
            "scenes": [{"id": s["id"], "name": s["name"]} for s in self.home.scenes.scenes],
            "occupancy": d.get("occupancy"), "person_count": d.get("person_count"),
            "owner_presence": ctx["owner_presence"], "security": ctx["security"],
            "modes": self.modes_fn(), "pending_proposals": len(self.ctx.pending.all()),
        }
        # Only real sensors: the runtime's neutral placeholders are not readings.
        for sensor in self.ctx.registry.sensors():
            snapshot[f"{sensor['id']}_reading"] = d.get(f"{sensor['id']}_state")
        snapshot.update(self.security_fn() or {})
        return snapshot

    def _tool_get_security_state(self, args, user, role):
        d = self.ctx.descriptor
        return {"modes": self.modes_fn(), "occupancy": d.get("occupancy"),
                "person_count": d.get("person_count"),
                "owner_presence": self.home.context()["owner_presence"],
                **(self.security_fn() or {})}

    def _tool_get_house_state(self, args, user, role):
        return self.state_snapshot()

    def _tool_set_device(self, args, user, role):
        device = self.ctx.registry.get(args.get("device"))
        if device is None:
            return {"error": f"no device {args.get('device')!r}"}
        ok, reason = self.home.set(device["id"], args.get("action"), source="assistant", actor=user)
        return {"ok": ok, "result": f"{device['name']} {args.get('action')}" if ok else reason,
                "_action": ok}

    def _tool_all_off(self, args, user, role):
        done, failed = self.home.all_off(source="assistant", actor=user, room=args.get("room"))
        return {"turned_off": done, "failed": failed,
                "result": f"Turned off {', '.join(done)}" if done else "nothing was on",
                "_action": bool(done)}

    def _tool_run_scene(self, args, user, role):
        scene = self.home.scenes.get(args.get("scene")) or self.home.scenes.find(args.get("scene", ""))
        if scene is None:
            return {"error": f"no scene {args.get('scene')!r}"}
        ok, reason, _ = self.home.run_scene(scene["id"], actor=user)
        return {"ok": ok, "reason": reason, "result": f"Scene {scene['name']}", "_action": True}

    def _tool_create_scene(self, args, user, role):
        if role != "admin":
            return {"error": "only an admin can save scenes; suggest they add it on the Home page"}
        ok, reason, scene = self.home.scenes.add(args.get("name", ""), args.get("actions") or [],
                                                 created_by=user)
        return {"ok": ok, "error": reason} if not ok else {
            "ok": True, "scene": scene, "result": f"Saved scene {scene['name']}", "_action": True}

    def _tool_schedule_action(self, args, user, role):
        target = ({"scene": args["scene"]} if args.get("scene")
                  else {"device": args.get("device"), "action": args.get("action")})
        if "scene" in target and self.home.scenes.get(target["scene"]) is None:
            found = self.home.scenes.find(target["scene"])
            if found:
                target = {"scene": found["id"]}
        now = self._clock()
        if args.get("in_minutes") is not None:
            try:
                minutes = float(args["in_minutes"])
            except (TypeError, ValueError):
                return {"error": "in_minutes must be a number"}
            ok, reason, entry = self.home.add_schedule(target, at=now + minutes * 60,
                                                       created_by=user)
        elif args.get("time"):
            if args.get("repeat"):
                if role != "admin":
                    return {"error": "only an admin can create recurring schedules; a one-off "
                                     "timer is allowed"}
                days = _DAY_SETS.get(args.get("days") or "daily")
                ok, reason, entry = self.home.add_schedule(target, time_hhmm=args["time"],
                                                           days=days, created_by=user)
            else:
                try:
                    hh, mm = map(int, str(args["time"]).split(":"))
                    lt = time.localtime(now)
                    at = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, hh, mm, 0, 0, 0, -1))
                except (ValueError, OverflowError):
                    return {"error": "time must be HH:MM"}
                if at <= now:
                    at += 86400
                ok, reason, entry = self.home.add_schedule(target, at=at, created_by=user)
        else:
            return {"error": "give in_minutes or time"}
        if not ok:
            return {"error": reason}
        when = (time.strftime("%H:%M", time.localtime(entry["at"])) if entry["kind"] == "once"
                else f"{entry['time']} ({args.get('days') or 'daily'})")
        return {"ok": True, "schedule_id": entry["id"],
                "result": f"Scheduled {self.home.describe_target(target)} at {when}",
                "_action": True}

    def _tool_list_schedules(self, args, user, role):
        return {"schedules": [{"id": e["id"], "what": e["describe"], "kind": e["kind"],
                               "time": e.get("time"), "days": e.get("days"),
                               "next_run": time.strftime("%a %H:%M", time.localtime(e["next_run"]))
                               if e.get("next_run") else None}
                              for e in self.home.schedule_view()]}

    def _tool_create_automation(self, args, user, role):
        instruction = str(args.get("instruction") or "").strip()[:500]
        if not instruction:
            return {"error": "no instruction"}
        rule, reason = self.ctx.nim.synthesize(instruction, self.ctx.store.rules, self.ctx.schema)
        if rule is None:
            return {"error": f"could not turn that into an automation: {reason}",
                    "vocabulary": sorted(self.ctx.schema.fields)}
        conflict = self.ctx.store.find_conflict(rule)
        proposal_id = self.ctx.pending.add(rule, conflict=conflict)
        rendered = render_rule(rule)
        return {"ok": True, "proposal": {"proposal_id": proposal_id, "rule": rule,
                                         "rendered": rendered, "conflict": conflict},
                "result": f"Drafted: when {rendered['when']} then {rendered['then']}. "
                          "It waits for confirmation on the Automations page.",
                "_action": True}

    def _tool_list_automations(self, args, user, role):
        return {"rules": [{"id": r.get("id"), "said": r.get("source_utterance"),
                           "enabled": r.get("enabled", True), **render_rule(r)}
                          for r in self.ctx.store.rules]}

    def _tool_set_security_mode(self, args, user, role):
        if self.set_mode_fn is None:
            return {"error": "modes are not available"}
        mode = args.get("mode")
        if mode not in MODE_NAMES or not isinstance(args.get("on"), bool):
            return {"error": "unknown mode"}
        message = self.set_mode_fn(mode, args["on"], user)
        return {"ok": True, "result": message, "_action": True}

    def _tool_recent_activity(self, args, user, role):
        limit = max(1, min(int(args.get("limit") or 10), 30))
        names = {d["id"]: d["name"] for d in self.ctx.registry.devices}
        rules = {r.get("id"): r.get("source_utterance") for r in self.ctx.store.rules}
        out = []
        for e in actuation_log.recent(self.ctx.log_path, limit=limit):
            cause = (f"rule: {rules.get(e['rule_id'], e['rule_id'])}" if e.get("rule_id")
                     else e.get("source") or "manual")
            out.append({"when": time.strftime("%a %H:%M", time.localtime(e["ts"])),
                        "device": names.get(e["device"], e["device"]), "action": e["action"],
                        "ok": e["ok"], "cause": cause, "by": e.get("actor", "")})
        return {"activity": out}

    def _tool_energy_usage(self, args, user, role):
        days = max(1, min(int(args.get("days") or 1), 30))
        u = self.home.usage(days=days)
        return {"days": days, "total_kwh": u["total_kwh"], "cost": u["cost"],
                "devices": [{"name": r["name"], "hours": r["hours"], "kwh": r["kwh"]}
                            for r in u["devices"]]}

    # ── memory ────────────────────────────────────────────────────────────────

    def _remember(self, user, text, reply):
        history = self._history.setdefault(user, deque(maxlen=HISTORY_TURNS))
        history.append({"role": "user", "content": text})
        history.append({"role": "assistant", "content": reply})

    def forget(self, user):
        self._history.pop(user, None)

    def status(self):
        return {"nim": self.chat.status() if self.chat else {"configured": False},
                "decision": self.decision.status(), "lanes": dict(self.stats),
                "actuators": sum(1 for d in self.ctx.registry.devices if is_actuator(d["type"]))}
