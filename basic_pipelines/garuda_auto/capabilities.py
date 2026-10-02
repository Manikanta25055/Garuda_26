"""Everything Narada can do, and everything it must not, in one table.

A capability is one thing a person can do on the site (a button, a toggle, a
form) offered to the model as a tool. Each one says who may use it, how much
care it needs and which routes it stands in for, so the list of what the agent
can reach is read here and not pieced together from handlers.

    tier  read     looks only
          change   changes something that is easy to put back
          confirm  shown on a card and done only after a person taps it
    lane  fast     offered on every turn to the quick model
          planner  offered to the bigger model that builds multi-step work

A capability either has a handler in the agent (the first ones, which act on
the house directly) or names the route that carries it out: `call`. The second
kind runs the site's own endpoint as the person who asked, so there is one
copy of each rule about who may do what.

Every route of the site is accounted for exactly once: by a capability, in
PLANNED (will become one), in NEVER (kept from the model on purpose) or in
PLUMBING (pages, streams, the conversation itself). tests/garuda_auto/
test_capabilities.py holds the list against tests/contract/routes.json, so a
new route cannot be added without deciding which of the four it is.
"""
from dataclasses import dataclass, field

from ..narada_brain.memory import CATEGORIES as MEMORY_CATEGORIES

MODE_NAMES = ("dnd", "night", "idle", "emergency", "privacy", "email_off")
TIERS = ("read", "change", "confirm")
LANES = ("fast", "planner")


@dataclass(frozen=True)
class Capability:
    name: str
    summary: str
    params: dict = field(default_factory=dict)
    required: tuple = ()
    role: str = "user"          # the least role that may use it
    tier: str = "read"
    lane: str = "fast"
    security: bool = False      # also offered on the security-only product
    routes: tuple = ()          # "METHOD /path" this stands in for
    typed: tuple = ()           # typed by the person on the confirm card, never by the model
    call: str = ""              # the one route that carries it out (site_calls.py);
                                # without it the agent has a handler of its own

    def tool(self):
        """The function description sent to the model."""
        return {"type": "function", "function": {
            "name": self.name, "description": self.summary,
            "parameters": {"type": "object", "properties": self.params,
                           "required": [r for r in self.required if r in self.params]}}}


_S = {"type": "string"}

CAPABILITIES = (
    Capability("get_security_state", "Security modes, alerts, camera, occupancy and presence.",
               security=True, routes=("GET /api/state",)),
    Capability("get_house_state",
               "Devices with state, scenes, occupancy, presence, security, modes.",
               routes=("GET /api/home/overview",)),
    Capability("set_device", "Switch one device on or off.",
               {"device": {"type": "string", "description": "device id"},
                "action": {"type": "string", "enum": ["on", "off"]}}, ("device", "action"),
               tier="change", routes=("POST /api/home/devices/{device_id}/set",)),
    Capability("all_off", "Turn off every device, optionally only in one room.",
               {"room": _S}, tier="change", routes=("POST /api/home/all-off",)),
    Capability("run_scene", "Run a saved scene.", {"scene": _S}, ("scene",),
               tier="change", routes=("POST /api/home/scenes/{scene_id}/run",)),
    Capability("create_scene", "Save a new scene (admin only).",
               {"name": _S,
                "actions": {"type": "array", "items": {"type": "object", "properties": {
                    "device": _S, "action": _S}}}},
               ("name", "actions"), role="admin", tier="change",
               routes=("POST /api/home/scenes",)),
    Capability("schedule_action",
               "Schedule a device action or a scene. Give in_minutes for a timer, or time "
               "(HH:MM 24h) with repeat=true for a recurring schedule (admin only).",
               {"device": _S, "action": {"type": "string", "enum": ["on", "off"]},
                "scene": _S, "in_minutes": {"type": "number"},
                "time": _S, "repeat": {"type": "boolean"},
                "days": {"type": "string", "enum": ["daily", "weekdays", "weekends"]}},
               tier="change", routes=("POST /api/home/schedules",)),
    Capability("list_schedules", "List schedules and timers.",
               routes=("GET /api/home/schedules",)),
    Capability("create_automation",
               "Draft a condition-based automation from the person's own sentence.",
               {"instruction": _S}, ("instruction",),
               tier="change", routes=("POST /api/home/instruct",)),
    Capability("list_automations", "List saved automations (rules).",
               routes=("GET /api/home/rules", "GET /api/home/automations")),
    Capability("set_security_mode", "Turn a Garuda security mode on or off.",
               {"mode": {"type": "string", "enum": list(MODE_NAMES)},
                "on": {"type": "boolean"}}, ("mode", "on"),
               tier="change", security=True,
               routes=("POST /api/modes", "POST /api/set-mode")),
    Capability("recent_activity", "Recent device actions with who or what caused them.",
               {"limit": {"type": "integer"}}, routes=("GET /api/home/activity",)),
    Capability("energy_usage", "Device on-time and estimated energy.",
               {"days": {"type": "integer"}}, routes=("GET /api/home/usage",)),
    Capability("remember_fact", "Keep one lasting fact about the household in memory.",
               {"text": {"type": "string",
                         "description": "one plain sentence that names who it is about"},
                "category": {"type": "string", "enum": list(MEMORY_CATEGORIES)},
                "replaces": {"type": "string",
                             "description": "id of the fact this corrects, if any"}},
               ("text",), security=True, routes=("POST /api/narada/memory",)),
    Capability("forget_fact", "Remove a fact from memory.",
               {"what": {"type": "string",
                         "description": "the fact's id, or words describing it"}},
               ("what",), tier="change", security=True,
               routes=("DELETE /api/narada/memory/{fact_id}",)),
)

def _site(name, summary, call, params=None, required=(), *, role="user", tier="read",
          security=False, typed=(), lane="planner"):
    """A capability carried out by the site's own endpoint; the planner's to use."""
    return Capability(name, summary, params or {}, tuple(required), role=role, tier=tier,
                      lane=lane, security=security, routes=(call,), typed=tuple(typed),
                      call=call)


_ID = lambda what: {"type": "string", "description": f"id of the {what}"}  # noqa: E731
_N = {"type": "integer"}

CAPABILITIES += (
    # schedules, automations, suggestions
    _site("delete_schedule", "Delete a schedule or timer (its creator or an admin).",
          "DELETE /api/home/schedules/{schedule_id}", {"schedule_id": _ID("schedule")},
          ["schedule_id"], tier="change"),
    _site("pause_schedule", "Pause a schedule, or resume a paused one.",
          "POST /api/home/schedules/{schedule_id}/toggle", {"schedule_id": _ID("schedule")},
          ["schedule_id"], tier="change"),
    _site("discard_proposal", "Throw away a drafted automation that is waiting to be confirmed.",
          "DELETE /api/home/proposals/{proposal_id}", {"proposal_id": _ID("proposal")},
          ["proposal_id"], tier="change"),
    _site("pause_automation", "Pause a saved automation, or resume a paused one.",
          "POST /api/home/rules/{rule_id}/toggle", {"rule_id": _ID("automation")},
          ["rule_id"], role="admin", tier="change"),
    _site("list_suggestions", "Routines the house has noticed and could turn into schedules.",
          "GET /api/home/suggestions"),
    _site("accept_suggestion", "Turn a noticed routine into a recurring schedule.",
          "POST /api/home/suggestions/{suggestion_id}/accept",
          {"suggestion_id": _ID("suggestion")}, ["suggestion_id"], role="admin", tier="change"),
    _site("dismiss_suggestion", "Stop offering a noticed routine.",
          "POST /api/home/suggestions/{suggestion_id}/dismiss",
          {"suggestion_id": _ID("suggestion")}, ["suggestion_id"], tier="change"),
    _site("dismiss_notice", "Clear a notice from the Home page.",
          "POST /api/home/notices/{notice_id}/dismiss", {"notice_id": _ID("notice")},
          ["notice_id"], tier="change"),
    # devices
    _site("list_device_types", "Kinds of device that can be added, and the free relay channels.",
          "GET /api/home/device-types"),
    _site("edit_device", "Rename a device, move it to a room, set its wattage, or enable/disable it.",
          "PATCH /api/home/devices/{device_id}",
          {"device_id": _ID("device"), "name": _S, "room": _S, "watts": {"type": "number"},
           "enabled": {"type": "boolean"}}, ["device_id"], role="admin", tier="change"),
    # what the house has been doing
    _site("daily_digest", "Today's summary of the house in words.", "GET /api/home/digest",
          {"refresh": {"type": "boolean"}}),
    _site("home_insights", "Energy use per device and the recent activity, over some days.",
          "GET /api/home/insights", {"days": _N, "limit": _N}),
    _site("get_home_settings", "Home settings: tariff, away and vacation behaviour, digest time.",
          "GET /api/home/settings"),
    _site("event_stats", "How many security events are stored and how many wait to be synced.",
          "GET /api/events/stats", security=True),
    _site("recent_events", "Security events (detections, alerts) after a time.",
          "GET /api/events/since",
          {"since": {"type": "string", "description": "ISO time; empty for the oldest"},
           "limit": _N}, security=True),
    _site("camera_health", "The camera pipeline's speed and health figures.",
          "GET /api/cascade_metrics", security=True),
    _site("read_logs", "The system, voice, presence and detection logs (newest lines).",
          "GET /api/logs", role="admin", security=True),
    _site("read_feedback", "Feedback people have sent from the site.", "GET /api/feedback",
          role="admin", security=True),
    _site("system_info", "Build, uptime, health checks, background workers and backups.",
          "GET /api/system/info", role="admin", security=True),
    # camera
    _site("start_clip", "Start recording a video clip from the camera.", "POST /api/clip/start",
          tier="change", security=True),
    _site("stop_clip", "Stop the clip that is recording and save it.", "POST /api/clip/stop",
          tier="change", security=True),
    # memory
    _site("list_memory", "Everything in memory: facts, ones waiting to be confirmed, removed ones.",
          "GET /api/narada/memory", security=True),
    _site("edit_fact", "Reword a fact in memory or move it to another category.",
          "PATCH /api/narada/memory/{fact_id}",
          {"fact_id": _ID("fact"), "text": _S,
           "category": {"type": "string", "enum": list(MEMORY_CATEGORIES)}},
          ["fact_id"], tier="change", security=True),
    _site("restore_fact", "Bring back a fact that was removed from memory.",
          "POST /api/narada/memory/{fact_id}/restore", {"fact_id": _ID("fact")}, ["fact_id"],
          tier="change", security=True),
    _site("confirm_fact", "Keep a fact that is waiting for the person's confirmation.",
          "POST /api/narada/memory/{fact_id}/confirm", {"fact_id": _ID("fact")}, ["fact_id"],
          tier="change", security=True),
    _site("mute_observation", "Stop making one kind of unprompted remark.",
          "POST /api/narada/observations/mute",
          {"key": {"type": "string", "description": "the remark's key"}}, ["key"],
          tier="change", security=True),
    # settings
    _site("get_security_settings",
          "Detection threshold, watched and danger labels, alert email, mode schedule, "
          "night presence window, taught voice commands.",
          "GET /api/config", role="admin", security=True),
    _site("add_voice_command", "Teach a phrase and the fixed reply Narada gives to it.",
          "POST /api/config/command/add", {"phrase": _S, "response": _S},
          ["phrase", "response"], role="admin", tier="change", security=True),
    _site("delete_voice_command", "Remove a taught phrase.", "POST /api/config/command/delete",
          {"phrase": _S}, ["phrase"], role="admin", tier="change", security=True),
    # presence
    _site("list_tracked_phones", "Phones whose presence means someone is home.",
          "GET /api/devices", role="admin", security=True),
    _site("refresh_presence", "Check right now who is home.", "POST /api/presence_refresh",
          role="admin", tier="change", security=True),
    _site("network_neighbours", "Devices seen on the home network, with their addresses.",
          "GET /api/arp", role="admin", security=True),
    # upkeep
    _site("send_test_email", "Send a test alert email.", "POST /api/email/test",
          role="admin", tier="change", security=True),
    _site("list_backups", "Saved backups of the house's data.", "GET /api/system/backups",
          role="admin", security=True),
    _site("create_backup", "Make a backup of the house's data now.", "POST /api/system/backups",
          role="admin", tier="change", security=True),
    _site("list_users", "People who can sign in, with their roles.", "GET /api/users",
          role="admin", security=True),
)
# Not a button on the site: these are the agent's own.
INTERNAL = frozenset({"hand_to_planner", "show_artifact"})

CAPABILITIES += (
    Capability("hand_to_planner",
               "Pass the request to the planner, a slower and more capable model with every "
               "capability of the site. Use it for anything you have no tool for: building a "
               "shortcut or automation, several steps that depend on each other, managing "
               "devices, people or settings, or showing a chart, table or small tool.",
               {"why": {"type": "string", "description": "a few words on what is needed"}}),
    Capability("show_artifact",
               "Show the person a page you write: a chart, table, dashboard or small tool. "
               "Give one complete, self-contained HTML document.",
               {"title": _S, "html": {"type": "string",
                                      "description": "a complete HTML document; inline CSS and "
                                                     "JavaScript only, no external URLs"}},
               ("title", "html"), tier="change", lane="planner", security=True),
)

_PROGRAM = {"type": "object", "description":
            'the shortcut: {"name", "description", "trigger", "conditions"?, "steps", '
            '"cooldown_s"?}. See shortcut_vocabulary for the grammar\'s facts and steps.'}

CAPABILITIES += (
    _site("list_shortcuts", "Saved shortcuts with their steps, and any that are running now.",
          "GET /api/home/shortcuts"),
    _site("shortcut_vocabulary",
          "What a shortcut can be built from: every fact a condition may test, with its value "
          "now, and every capability a step may use.",
          "GET /api/home/shortcuts/vocabulary"),
    _site("check_shortcut", "Check a shortcut without saving it; returns the problem, or the "
                            "shortcut said back in plain lines.",
          "POST /api/home/shortcuts/check", {"program": _PROGRAM}, ["program"]),
    # Saying a shortcut's name is an everyday command, so the quick model has this one.
    _site("run_shortcut", "Run a saved shortcut now.", "POST /api/home/shortcuts/{shortcut_id}/run",
          {"shortcut_id": {"type": "string", "description": "the shortcut's id or its name"}},
          ["shortcut_id"], tier="change", lane="fast"),
    _site("cancel_shortcut", "Stop a shortcut that is running.",
          "POST /api/home/shortcuts/{shortcut_id}/cancel", {"shortcut_id": _ID("shortcut")},
          ["shortcut_id"], tier="change"),
    _site("pause_shortcut", "Pause a shortcut's trigger, or resume a paused one.",
          "POST /api/home/shortcuts/{shortcut_id}/toggle", {"shortcut_id": _ID("shortcut")},
          ["shortcut_id"], tier="change"),
    # A shortcut is saved, changed or removed only after the person has read it on a card.
    _site("create_shortcut", "Save a new shortcut. The person sees it on a card and confirms.",
          "POST /api/home/shortcuts", {"program": _PROGRAM}, ["program"], tier="confirm"),
    _site("update_shortcut", "Replace a saved shortcut with a changed version.",
          "PATCH /api/home/shortcuts/{shortcut_id}",
          {"shortcut_id": _ID("shortcut"), "program": _PROGRAM}, ["shortcut_id", "program"],
          tier="confirm"),
    _site("delete_shortcut", "Delete a saved shortcut.", "DELETE /api/home/shortcuts/{shortcut_id}",
          {"shortcut_id": _ID("shortcut")}, ["shortcut_id"], tier="confirm"),
)

# Done only after the person taps Confirm on a card (confirmations.py). The
# model proposes; it cannot carry these out.
CAPABILITIES += (
    _site("confirm_proposal", "Save a drafted automation so it starts running.",
          "POST /api/home/proposals/{proposal_id}/confirm", {"proposal_id": _ID("proposal")},
          ["proposal_id"], role="admin", tier="confirm"),
    _site("delete_automation", "Delete a saved automation.", "DELETE /api/home/rules/{rule_id}",
          {"rule_id": _ID("automation")}, ["rule_id"], role="admin", tier="confirm"),
    _site("delete_scene", "Delete a saved scene.", "DELETE /api/home/scenes/{scene_id}",
          {"scene_id": _ID("scene")}, ["scene_id"], role="admin", tier="confirm"),
    _site("add_device", "Add a device to the house. See list_device_types for kinds and channels.",
          "POST /api/home/devices",
          {"id": {"type": "string", "description": "short id, lowercase, no spaces"},
           "name": _S, "type": _S, "room": _S,
           "transport": {"type": "object",
                         "description": '{"kind": "relay", "channel": n} or '
                                        '{"kind": "mqtt", "topic": "..."}'},
           "watts": {"type": "number"}},
          ["id", "name", "type", "room", "transport"], role="admin", tier="confirm"),
    _site("delete_device", "Remove a device from the house; automations using it stop.",
          "DELETE /api/home/devices/{device_id}", {"device_id": _ID("device")}, ["device_id"],
          role="admin", tier="confirm"),
    _site("change_home_settings",
          "Change home settings (see get_home_settings for the names and current values).",
          "POST /api/home/settings",
          {"settings": {"type": "object", "description": "only the settings to change"}},
          ["settings"], role="admin", tier="confirm"),
    _site("change_security_settings",
          "Change detection and alert settings; give only what should change.",
          "POST /api/config",
          {"detection_threshold": {"type": "number", "description": "0.05 to 0.95"},
           "danger_labels": {"type": "array", "items": _S},
           "watch_labels": {"type": "array", "items": _S},
           "email_recipients": {"type": "array", "items": _S},
           "email_cooldown": {"type": "integer", "description": "seconds, 5 to 3600"},
           "mode_schedule": {"type": "object",
                             "description": '{"dnd|email_off|idle|night": {"start": "HH:MM", '
                                            '"end": "HH:MM"}}; replaces the whole schedule'},
           "night_presence_start": _S, "night_presence_end": _S,
           "night_presence_enabled": {"type": "boolean"}},
          role="admin", tier="confirm", security=True),
    _site("add_tracked_phone", "Track a phone on the network as a sign that someone is home.",
          "POST /api/devices/add",
          {"name": _S, "mac": {"type": "string", "description": "aa:bb:cc:dd:ee:ff"}},
          ["name", "mac"], role="admin", tier="confirm", security=True),
    _site("delete_tracked_phone", "Stop tracking a phone.", "POST /api/devices/delete",
          {"mac": _S}, ["mac"], role="admin", tier="confirm", security=True),
    _site("emergency_stop", "Stop the whole Garuda system: camera, detection and this site.",
          "POST /api/emergency-stop", role="admin", tier="confirm", security=True),
    # People. A password is typed on the card; the model never sees or sets one.
    _site("add_user", "Add a person who can sign in (never an admin).", "POST /api/users/add",
          {"username": {"type": "string", "description": "3-32 letters, digits, _ or -"},
           "display_name": _S,
           "box_color": {"type": "string", "description": "like #1565c0"}},
          ["username", "password"], role="admin", tier="confirm", security=True,
          typed=("password",)),
    _site("update_user", "Change a person's display name or colour; a new password, if "
                         "wanted, is typed by the admin on the card.",
          "POST /api/users/update",
          {"username": _S, "display_name": _S, "box_color": _S}, ["username"],
          role="admin", tier="confirm", security=True, typed=("new_password",)),
    _site("delete_user", "Remove a person's sign-in.", "POST /api/users/delete",
          {"username": _S}, ["username"], role="admin", tier="confirm", security=True),
)

BY_NAME = {c.name: c for c in CAPABILITIES}


# Kept from the planner's tool list. It is already there; a shortcut does all a
# rule does, and with both offered the models drafted a rule first and a
# shortcut after; create_shortcut checks itself and the facts are in its
# instructions, so the two helpers only cost a round trip each.
PLANNER_HIDDEN = frozenset({"hand_to_planner", "create_automation", "check_shortcut",
                            "shortcut_vocabulary"})


def tools(lane="fast", security_only=False):
    """What one lane is offered. The planner also gets everything the fast lane
    has, except what PLANNER_HIDDEN names."""
    return [c.tool() for c in CAPABILITIES
            if (c.lane == lane or lane == "planner") and (c.security or not security_only)
            and not (lane == "planner" and c.name in PLANNER_HIDDEN)]


def names(*, security=None, changing=None):
    """Capability names, narrowed by the flags that are given."""
    out = []
    for c in CAPABILITIES:
        if security is not None and c.security != security:
            continue
        if changing is not None and (c.tier != "read") != changing:
            continue
        out.append(c.name)
    return tuple(out)


# Routes that will become capabilities: route -> (capability, least role, tier).
# The plan for the next step, kept here so the coverage test stays honest.
PLANNED = {
    # schedules, automations, scenes
    # devices
    # what the house has been doing
    # camera
    "GET /api/snapshot": ("take_snapshot", "user", "read"),
    # memory
    # settings
    # presence
    # upkeep
    # people (the owner's call, 2026-10-02: reachable, behind a confirm card;
    # a password is typed into the card, never into the conversation)
}

# Kept from the model on purpose: route -> why.
NEVER = {
    "POST /api/login": "signing in is a person's act",
    "POST /api/logout": "signing in is a person's act",
    "POST /api/refresh": "signing in is a person's act",
    "GET /api/session": "signing in is a person's act",
    "GET /api/users-public": "signing in is a person's act",
    "POST /api/admin/send-otp": "one-time codes prove a person is present",
    "POST /api/admin/verify-otp": "one-time codes prove a person is present",
    "POST /api/forgot/send-otp": "one-time codes prove a person is present",
    "POST /api/forgot/reset": "one-time codes prove a person is present",
    "POST /api/master_key/login": "master keys open the house without a password",
    "POST /api/master_key/verify": "master keys open the house without a password",
    "GET /api/master_keys": "master keys open the house without a password",
    "POST /api/master_key/request_otp": "master keys open the house without a password",
    "POST /api/master_key/add": "master keys open the house without a password",
    "POST /api/master_key/delete": "master keys open the house without a password",
    "GET /api/home/ai": "the model does not handle its own keys",
    "POST /api/home/ai": "the model does not handle its own keys",
    "POST /api/home/ai/test": "the model does not handle its own keys",
    "GET /api/eval/fps_probe": "test harness",
    "POST /api/eval/inject_danger": "test harness",
    "POST /api/eval/tag": "test harness",
    "GET /api/logs/download": "a file for a person; read_logs covers the content",
    "GET /api/openapi.json": "developer page",
    "POST /api/feedback": "a person's own words to the owner",
}

# Not actions at all: pages, liveness, streams, and the conversation itself.
PLUMBING = frozenset({
    "GET /", "GET /favicon.ico", "GET /manifest.json", "GET /sw.js", "- /static",
    "GET /api/health", "GET /api/ready", "GET /api/meta", "GET /api/heartbeat",
    "GET /stream", "POST /webrtc/offer", "WS /ws", "WS /ws/stream", "WS /ws/narada-voice",
    "GET /api/events/pending",          # the offline client's own sync; it marks events as sent
    "POST /api/chat", "POST /api/chat/stream", "GET /api/narada/progress",
    "POST /api/narada/actions/{action_id}/confirm", "POST /api/narada/actions/{action_id}/cancel",
    # Artifacts are made by show_artifact; these show, keep and serve them.
    "GET /api/narada/artifacts", "GET /api/narada/artifacts/{artifact_id}/view",
    "POST /api/narada/artifacts/{artifact_id}/call", "POST /api/narada/artifacts/{artifact_id}/pin",
    "DELETE /api/narada/artifacts/{artifact_id}", "GET /api/narada/info",
    "POST /api/narada/voice/token",
})


def covered_routes():
    return {route for c in CAPABILITIES for route in c.routes}
