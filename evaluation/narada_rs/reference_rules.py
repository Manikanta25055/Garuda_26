"""Reference rules for the scripted corpus, one per corpus entry.

Written before any synthesis output was seen, for the same reason the
paraphrases were: a reference adjusted after the fact measures nothing.

Each entry gives the conditions (all must hold), the actions, and `vague` --
the numeric fields whose threshold the utterance does not state ("dark",
"warm", "afternoon"). For those fields a synthesised rule is not marked wrong
for picking a different number; the scorer substitutes the reference constant
before comparing, and the report keeps exact and threshold-normalised
agreement apart.
"""


def _c(field, op, value):
    return {"field": field, "op": op, "value": value}


def _a(device, action):
    return {"device": device, "action": action}


EMPTY = _c("occupancy", "==", "empty")
OCCUPIED = _c("occupancy", "==", "occupied")
DARK = _c("ambient_luma", "<", 60)
BRIGHT = _c("ambient_luma", ">", 150)

REFERENCE = {
    "c01": {"when": [EMPTY, _c("occupancy_duration_s", ">=", 300)], "then": [_a("fan", "off")], "vague": []},
    "c02": {"when": [_c("zone", "==", "desk"), _c("posture", "==", "seated")], "then": [_a("lamp", "on")], "vague": []},
    "c03": {"when": [OCCUPIED, _c("temperature_c", ">", 30)], "then": [_a("fan", "on")], "vague": []},
    "c04": {"when": [DARK, OCCUPIED], "then": [_a("lamp", "on")], "vague": ["ambient_luma"]},
    "c05": {"when": [_c("hour", ">=", 21)], "then": [_a("lamp", "off")], "vague": []},
    "c06": {"when": [_c("temperature_c", "<", 25)], "then": [_a("fan", "off")], "vague": []},
    "c07": {"when": [_c("zone", "==", "door"), _c("posture", "==", "walking")], "then": [_a("lamp", "on")], "vague": []},
    "c08": {"when": [EMPTY, _c("occupancy_duration_s", ">=", 600)], "then": [_a("fan", "off"), _a("lamp", "off")], "vague": []},
    "c09": {"when": [_c("humidity_pct", ">", 70)], "then": [_a("fan", "on")], "vague": []},
    "c10": {"when": [BRIGHT], "then": [_a("lamp", "off")], "vague": ["ambient_luma"]},
    "c11": {"when": [_c("person_count", ">", 2)], "then": [_a("fan", "on")], "vague": []},
    "c12": {"when": [_c("hour", "==", 0), _c("lamp_state", "==", "on")], "then": [_a("lamp", "off")], "vague": []},
    "c13": {"when": [_c("hour", "==", 6)], "then": [_a("fan", "off")], "vague": []},
    "c14": {"when": [_c("zone", "==", "door"), _c("posture", "==", "standing"), DARK], "then": [_a("lamp", "on")], "vague": ["ambient_luma"]},
    "c15": {"when": [_c("zone", "==", "desk"), _c("posture", "==", "seated")], "then": [_a("fan", "on")], "vague": []},
    "c16": {"when": [_c("temperature_c", "<", 22), EMPTY], "then": [_a("fan", "off")], "vague": ["temperature_c"]},
    "c17": {"when": [_c("zone", "==", "center"), DARK], "then": [_a("lamp", "on")], "vague": ["ambient_luma"]},
    "c18": {"when": [_c("humidity_pct", ">", 60), _c("temperature_c", ">", 28)], "then": [_a("fan", "on")], "vague": []},
    "c19": {"when": [EMPTY, _c("occupancy_duration_s", ">=", 120)], "then": [_a("lamp", "off")], "vague": []},
    "c20": {"when": [_c("hour", ">=", 12), _c("hour", "<", 17), OCCUPIED], "then": [_a("fan", "on")], "vague": ["hour"]},
    "c21": {"when": [_c("fan_state", "==", "off"), EMPTY], "then": [_a("lamp", "off")], "vague": []},
    "c22": {"when": [_c("lamp_state", "==", "off"), _c("hour", ">=", 23)], "then": [_a("fan", "off")], "vague": []},
    "c23": {"when": [_c("person_count", ">", 1)], "then": [_a("lamp", "on")], "vague": []},
    "c24": {"when": [_c("temperature_c", ">", 32)], "then": [_a("fan", "on")], "vague": []},
    "c25": {"when": [BRIGHT, _c("hour", ">=", 6), _c("hour", "<", 18)], "then": [_a("lamp", "off")], "vague": ["ambient_luma", "hour"]},
    "c26": {"when": [_c("humidity_pct", "<", 40)], "then": [_a("fan", "off")], "vague": []},
    "c27": {"when": [_c("posture", "==", "standing"), _c("occupancy_duration_s", ">=", 30)], "then": [_a("lamp", "on")], "vague": []},
    "c28": {"when": [_c("zone", "==", "desk"), _c("temperature_c", ">", 28)], "then": [_a("fan", "on")], "vague": ["temperature_c"]},
    "c29": {"when": [_c("posture", "==", "walking"), DARK], "then": [_a("fan", "on"), _a("lamp", "on")], "vague": ["ambient_luma"]},
    "c30": {"when": [EMPTY, _c("occupancy_duration_s", ">=", 3600)], "then": [_a("fan", "off")], "vague": []},
}


def as_rule(corpus_id, utterance=""):
    ref = REFERENCE[corpus_id]
    return {
        "id": f"ref_{corpus_id}",
        "source_utterance": utterance or corpus_id,
        "when": {"all": [dict(c) for c in ref["when"]]},
        "then": [dict(a) for a in ref["then"]],
        "cooldown_s": 60,
        "enabled": True,
    }
