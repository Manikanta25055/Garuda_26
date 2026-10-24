"""Per-object rules and zones: what each thing seen does, when, and where."""
import types

import numpy as np
import pytest

import conftest  # noqa: F401
import Garuda_web as gw
import test_audit_2026_10_24 as audit
from basic_pipelines.garuda_routes import zones

DANGER = {"knife", "scissors", "hammer"}
COUNTER = {"id": "c", "name": "Counter", "rect": [0.0, 0.0, 0.5, 0.5], "mode": "quiet", "labels": ["knife"]}
DOOR = {"id": "d", "name": "Back door", "rect": [0.5, 0.5, 1.0, 1.0], "mode": "alert", "labels": ["person"]}
LEFT_BOX, RIGHT_BOX = (0.1, 0.1, 0.3, 0.3), (0.6, 0.6, 0.9, 0.9)


# ── decide(): the answer for one detection ───────────────────────────────────

def test_nothing_saved_keeps_todays_behaviour():
    empty = {"rules": {}, "zones": []}
    assert zones.decide(empty, "Knife", LEFT_BOX, "home", DANGER) == "alert"
    assert zones.decide(empty, "person", LEFT_BOX, "away", DANGER) == "log"


def test_a_rule_sets_the_action_per_arm_state():
    data = {"rules": {"knife": {"home": "log", "away": "alert"}}, "zones": []}
    assert zones.decide(data, "knife", None, "home", DANGER) == "log"
    assert zones.decide(data, "knife", None, "away", DANGER) == "alert"
    assert zones.decide(data, "knife", None, "night", DANGER) == "alert"    # unset: the default


def test_a_quiet_zone_turns_an_alert_into_a_log_only_inside_it():
    data = {"rules": {}, "zones": [COUNTER]}
    assert zones.decide(data, "knife", LEFT_BOX, "home", DANGER) == "log"
    assert zones.decide(data, "knife", RIGHT_BOX, "home", DANGER) == "alert"
    assert zones.decide(data, "scissors", LEFT_BOX, "home", DANGER) == "alert"   # not named


def test_an_alert_zone_raises_anything_it_names():
    data = {"rules": {}, "zones": [DOOR]}
    assert zones.decide(data, "person", RIGHT_BOX, "home", DANGER) == "alert"
    assert zones.decide(data, "person", LEFT_BOX, "home", DANGER) == "log"


def test_quiet_wins_where_zones_overlap_and_ignore_stays_ignored():
    everywhere = dict(DOOR, rect=[0, 0, 1, 1], labels=[])
    data = {"rules": {"cat": {"home": "ignore"}}, "zones": [everywhere, dict(COUNTER, labels=[])]}
    assert zones.decide(data, "knife", LEFT_BOX, "home", DANGER) == "log"
    assert zones.decide(data, "cat", LEFT_BOX, "home", DANGER) == "ignore"


# ── the API ──────────────────────────────────────────────────────────────────

def test_admin_saves_rules_and_zones_and_they_are_cleaned(app_client, admin_headers):
    r = app_client.post('/api/detection/rules', headers=admin_headers,
                        json={'rules': {'Knife': {'home': 'log', 'away': 'shout', 'off': 'alert'}, 'cat': 'x'}})
    assert r.status_code == 200
    assert r.json()['rules'] == {'knife': {'home': 'log'}}
    r = app_client.post('/api/detection/zones', headers=admin_headers,
                        json={'zones': [{'name': 'Counter', 'rect': [0.5, 0.5, -1, 0.1],
                                         'mode': 'quiet', 'labels': ['Knife']}]})
    z = r.json()['zones'][0]
    assert r.status_code == 200 and z['rect'] == [0.0, 0.1, 0.5, 0.5] and z['labels'] == ['knife'] and z['id']
    assert gw.RULES.data['zones'][0]['name'] == 'Counter'


def test_bad_zones_are_refused(app_client, admin_headers):
    for zone in ({'rect': [0, 0, 0.01, 0.01], 'mode': 'quiet'}, {'rect': [0, 0, 1], 'mode': 'quiet'},
                 {'rect': [0, 0, 1, 1], 'mode': 'loud'}):
        assert app_client.post('/api/detection/zones', json={'zones': [zone]},
                               headers=admin_headers).status_code == 400


def test_a_user_reads_the_rules_but_only_an_admin_changes_them(app_client, user_headers):
    r = app_client.get('/api/detection/rules', headers=user_headers)
    assert r.status_code == 200 and 'danger_labels' in r.json()
    assert app_client.post('/api/detection/rules', json={'rules': {}}, headers=user_headers).status_code == 403
    assert app_client.post('/api/detection/zones', json={'zones': []}, headers=user_headers).status_code == 403


def test_rules_need_a_sign_in(app_client):
    assert app_client.get('/api/detection/rules').status_code == 401


def test_rules_survive_a_restart(tmp_path):
    store = zones.ZoneStore(str(tmp_path / 'r.json'))
    store.replace(rules={'knife': {'home': 'log'}})
    assert zones.ZoneStore(str(tmp_path / 'r.json')).data['rules'] == {'knife': {'home': 'log'}}


# ── the frame callback follows them ──────────────────────────────────────────

@pytest.fixture
def quiet_alarm(monkeypatch):
    for name in ("trigger_software_alert", "email_after_second_look", "log_scissors_detection",
                 "_append_detection_perm", "log_system_update"):
        monkeypatch.setattr(gw, name, lambda *a, **k: None)
    monkeypatch.setattr(gw.STATE.alerts, "danger_active", False)
    monkeypatch.setattr(gw.STATE.alerts, "active", False)
    monkeypatch.setattr(gw.STATE.alerts, "email_held_at", 0.0)
    monkeypatch.setattr(gw.STATE.camera, "watch_last_logged", {})


def _two_frames(monkeypatch):
    # _Det's box is (0.1, 0.1, 0.3, 0.3): inside COUNTER.
    _, run = audit._callback_env(monkeypatch, [audit._Det("Knife")], np.zeros((64, 64, 3), np.uint8), False)
    run()
    run()


def test_a_knife_alarms_with_no_rules(monkeypatch, quiet_alarm):
    _two_frames(monkeypatch)
    assert gw.STATE.alerts.danger_active is True


def test_a_knife_in_a_quiet_zone_does_not_alarm(monkeypatch, quiet_alarm):
    monkeypatch.setattr(gw.RULES, "data", {"rules": {}, "zones": [COUNTER]})
    _two_frames(monkeypatch)
    assert gw.STATE.alerts.danger_active is False


def test_a_rule_for_the_arm_state_silences_it(monkeypatch, quiet_alarm):
    monkeypatch.setattr(gw.RULES, "data", {"rules": {"knife": {"night": "ignore"}}, "zones": []})
    monkeypatch.setattr(zones, "arm_current", lambda core, store: {"state": "night"})
    _two_frames(monkeypatch)
    assert gw.STATE.alerts.danger_active is False
