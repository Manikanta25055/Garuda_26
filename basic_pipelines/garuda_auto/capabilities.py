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
    call: str = ""              # the one route that carries it out (site_calls.py);
                                # without it the agent has a handler of its own

    def tool(self):
        """The function description sent to the model."""
        return {"type": "function", "function": {
            "name": self.name, "description": self.summary,
            "parameters": {"type": "object", "properties": self.params,
                           "required": list(self.required)}}}


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
          security=False):
    """A capability carried out by the site's own endpoint; the planner's to use."""
    return Capability(name, summary, params or {}, tuple(required), role=role, tier=tier,
                      lane="planner", security=security, routes=(call,), call=call)


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

BY_NAME = {c.name: c for c in CAPABILITIES}


def tools(lane="fast", security_only=False):
    """What one lane is offered. The planner also gets everything the fast lane has."""
    return [c.tool() for c in CAPABILITIES
            if (c.lane == lane or lane == "planner") and (c.security or not security_only)]


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
    "POST /api/home/proposals/{proposal_id}/confirm": ("confirm_proposal", "admin", "confirm"),
    "DELETE /api/home/rules/{rule_id}": ("delete_automation", "admin", "confirm"),
    "DELETE /api/home/scenes/{scene_id}": ("delete_scene", "admin", "confirm"),
    # devices
    "POST /api/home/devices": ("add_device", "admin", "confirm"),
    "DELETE /api/home/devices/{device_id}": ("delete_device", "admin", "confirm"),
    # what the house has been doing
    # camera
    "GET /api/snapshot": ("take_snapshot", "user", "read"),
    # memory
    # settings
    "POST /api/home/settings": ("change_home_settings", "admin", "confirm"),
    "POST /api/config": ("change_security_settings", "admin", "confirm"),
    "POST /api/emergency-stop": ("emergency_stop", "admin", "confirm"),
    # presence
    "POST /api/devices/add": ("add_tracked_phone", "admin", "confirm"),
    "POST /api/devices/delete": ("delete_tracked_phone", "admin", "confirm"),
    # upkeep
    # people (the owner's call, 2026-10-02: reachable, behind a confirm card;
    # a password is typed into the card, never into the conversation)
    "POST /api/users/add": ("add_user", "admin", "confirm"),
    "POST /api/users/update": ("update_user", "admin", "confirm"),
    "POST /api/users/delete": ("delete_user", "admin", "confirm"),
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
    "POST /api/chat", "POST /api/chat/stream", "GET /api/narada/info",
    "POST /api/narada/voice/token",
})


def covered_routes():
    return {route for c in CAPABILITIES for route in c.routes}
