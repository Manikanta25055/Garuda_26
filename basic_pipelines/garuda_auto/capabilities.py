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

BY_NAME = {c.name: c for c in CAPABILITIES}


def tools(lane="fast", security_only=False):
    return [c.tool() for c in CAPABILITIES
            if c.lane == lane and (c.security or not security_only)]


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
    "DELETE /api/home/schedules/{schedule_id}": ("delete_schedule", "user", "change"),
    "POST /api/home/schedules/{schedule_id}/toggle": ("pause_schedule", "user", "change"),
    "DELETE /api/home/proposals/{proposal_id}": ("discard_proposal", "user", "change"),
    "POST /api/home/proposals/{proposal_id}/confirm": ("confirm_proposal", "admin", "confirm"),
    "POST /api/home/rules/{rule_id}/toggle": ("pause_automation", "admin", "change"),
    "DELETE /api/home/rules/{rule_id}": ("delete_automation", "admin", "confirm"),
    "DELETE /api/home/scenes/{scene_id}": ("delete_scene", "admin", "confirm"),
    "GET /api/home/suggestions": ("list_suggestions", "user", "read"),
    "POST /api/home/suggestions/{suggestion_id}/accept": ("accept_suggestion", "admin", "change"),
    "POST /api/home/suggestions/{suggestion_id}/dismiss": ("dismiss_suggestion", "user", "change"),
    "POST /api/home/notices/{notice_id}/dismiss": ("dismiss_notice", "user", "change"),
    # devices
    "GET /api/home/device-types": ("list_device_types", "user", "read"),
    "POST /api/home/devices": ("add_device", "admin", "confirm"),
    "PATCH /api/home/devices/{device_id}": ("edit_device", "admin", "change"),
    "DELETE /api/home/devices/{device_id}": ("delete_device", "admin", "confirm"),
    # what the house has been doing
    "GET /api/home/digest": ("daily_digest", "user", "read"),
    "GET /api/home/insights": ("home_insights", "user", "read"),
    "GET /api/events/stats": ("event_stats", "user", "read"),
    "GET /api/events/since": ("recent_events", "user", "read"),
    "GET /api/events/pending": ("event_stats", "user", "read"),
    "GET /api/cascade_metrics": ("camera_health", "user", "read"),
    "GET /api/logs": ("read_logs", "admin", "read"),
    "GET /api/feedback": ("read_feedback", "admin", "read"),
    "GET /api/system/info": ("system_info", "admin", "read"),
    # camera
    "GET /api/snapshot": ("take_snapshot", "user", "read"),
    "POST /api/clip/start": ("start_clip", "user", "change"),
    "POST /api/clip/stop": ("stop_clip", "user", "change"),
    # memory
    "GET /api/narada/memory": ("list_memory", "user", "read"),
    "PATCH /api/narada/memory/{fact_id}": ("edit_fact", "user", "change"),
    "POST /api/narada/memory/{fact_id}/restore": ("restore_fact", "user", "change"),
    "POST /api/narada/memory/{fact_id}/confirm": ("confirm_fact", "user", "change"),
    "POST /api/narada/observations/mute": ("mute_observation", "user", "change"),
    # settings
    "GET /api/home/settings": ("get_home_settings", "user", "read"),
    "POST /api/home/settings": ("change_home_settings", "admin", "confirm"),
    "GET /api/config": ("get_security_settings", "admin", "read"),
    "POST /api/config": ("change_security_settings", "admin", "confirm"),
    "POST /api/config/command/add": ("add_voice_command", "admin", "change"),
    "POST /api/config/command/delete": ("delete_voice_command", "admin", "change"),
    "POST /api/emergency-stop": ("emergency_stop", "admin", "confirm"),
    # presence
    "GET /api/devices": ("list_tracked_phones", "admin", "read"),
    "POST /api/devices/add": ("add_tracked_phone", "admin", "confirm"),
    "POST /api/devices/delete": ("delete_tracked_phone", "admin", "confirm"),
    "POST /api/presence_refresh": ("refresh_presence", "admin", "change"),
    "GET /api/arp": ("network_neighbours", "admin", "read"),
    # upkeep
    "POST /api/email/test": ("send_test_email", "admin", "change"),
    "GET /api/system/backups": ("list_backups", "admin", "read"),
    "POST /api/system/backups": ("create_backup", "admin", "change"),
    # people (the owner's call, 2026-10-02: reachable, behind a confirm card;
    # a password is typed into the card, never into the conversation)
    "GET /api/users": ("list_users", "admin", "read"),
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
    "POST /api/chat", "POST /api/chat/stream", "GET /api/narada/info",
    "POST /api/narada/voice/token",
})


def covered_routes():
    return {route for c in CAPABILITIES for route in c.routes}
