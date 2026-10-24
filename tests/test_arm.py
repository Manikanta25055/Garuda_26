"""The arm switch: Off, Home, Night and Away over the mode flags, and auto-arm."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'basic_pipelines'))
import Garuda_web as gw  # noqa: E402
from basic_pipelines.garuda_routes import arm  # noqa: E402


def test_each_state_sets_the_flags_the_detector_reads(app_client, admin_headers):
    for state, idle, night in (("off", True, False), ("home", False, False),
                               ("night", False, True), ("away", False, True)):
        r = app_client.post('/api/arm', json={'state': state}, headers=admin_headers)
        assert r.status_code == 200 and r.json()['state'] == state
        modes = app_client.get('/api/state', headers=admin_headers).json()['modes']
        assert (modes['idle'], modes['night']) == (idle, night)
    app_client.post('/api/arm', json={'state': 'home'}, headers=admin_headers)


def test_only_an_admin_turns_the_system_off(app_client, user_headers):
    r = app_client.post('/api/arm', json={'state': 'off'}, headers=user_headers)
    assert r.status_code == 403
    assert app_client.post('/api/arm', json={'state': 'away'}, headers=user_headers).status_code == 200
    assert app_client.get('/api/arm', headers=user_headers).json()['state'] == 'away'
    app_client.post('/api/arm', json={'state': 'home'}, headers=user_headers)


def test_arm_needs_a_sign_in(app_client):
    assert app_client.get('/api/arm').status_code == 401
    assert app_client.post('/api/arm', json={'state': 'away'}).status_code == 401


def test_an_unknown_state_is_refused(app_client, admin_headers):
    assert app_client.post('/api/arm', json={'state': 'party'}, headers=admin_headers).status_code == 400


def test_a_flag_changed_elsewhere_wins(app_client, admin_headers):
    app_client.post('/api/arm', json={'state': 'away'}, headers=admin_headers)
    app_client.post('/api/modes', json={'mode': 'night', 'value': False}, headers=admin_headers)
    assert app_client.get('/api/arm', headers=admin_headers).json()['state'] == 'home'


def test_the_choice_survives_a_restart(tmp_path):
    store = arm.ArmStore(str(tmp_path / 'arm.json'))
    store.update(chosen='away', by='admin')
    assert arm.ArmStore(str(tmp_path / 'arm.json')).data['chosen'] == 'away'


@pytest.mark.parametrize("idle,night,chosen,want", [
    (True, True, "away", "off"), (False, True, "away", "away"),
    (False, True, "home", "night"), (False, False, "away", "home")])
def test_state_from_flags(idle, night, chosen, want):
    assert arm.state_from_flags(idle, night, chosen) == want


class _Stop(Exception):
    pass


def _run_loop(app_client, presence_steps, monkeypatch):
    """Drive auto_arm_loop through presence readings, one per tick."""
    steps = iter(presence_steps)
    monkeypatch.setattr(gw.STATE.presence, 'owner_present', next(steps))

    def tick(_):
        try:
            gw.STATE.presence.owner_present = next(steps)
        except StopIteration:
            raise _Stop
    with pytest.raises(_Stop):
        arm.auto_arm_loop(gw, gw.ARM, sleep=tick)


def test_auto_arm_follows_the_owners_phone(app_client, admin_headers, monkeypatch):
    app_client.post('/api/arm', json={'state': 'home'}, headers=admin_headers)
    _run_loop(app_client, [True, False, False], monkeypatch)       # leaves
    assert app_client.get('/api/arm', headers=admin_headers).json()['state'] == 'away'
    _run_loop(app_client, [False, True, True], monkeypatch)        # comes back
    assert app_client.get('/api/arm', headers=admin_headers).json()['state'] == 'home'


def test_auto_arm_never_undoes_a_choice_made_by_hand(app_client, admin_headers, monkeypatch):
    app_client.post('/api/arm', json={'state': 'away'}, headers=admin_headers)   # by hand
    _run_loop(app_client, [False, True, True], monkeypatch)
    assert app_client.get('/api/arm', headers=admin_headers).json()['state'] == 'away'
    app_client.post('/api/arm', json={'state': 'night'}, headers=admin_headers)
    _run_loop(app_client, [True, False, False], monkeypatch)       # Night is not Home
    assert app_client.get('/api/arm', headers=admin_headers).json()['state'] == 'night'
    app_client.post('/api/arm', json={'state': 'home'}, headers=admin_headers)


def test_auto_arm_can_be_switched_off(app_client, admin_headers, user_headers, monkeypatch):
    assert app_client.post('/api/arm/auto', json={'enabled': False}, headers=user_headers).status_code == 403
    assert app_client.post('/api/arm/auto', json={'enabled': False}, headers=admin_headers).json()['auto'] is False
    app_client.post('/api/arm', json={'state': 'home'}, headers=admin_headers)
    _run_loop(app_client, [True, False, False], monkeypatch)
    assert app_client.get('/api/arm', headers=admin_headers).json()['state'] == 'home'


def test_away_raises_the_presence_alarm_at_any_hour(app_client, admin_headers, monkeypatch):
    monkeypatch.setattr(gw.STATE.config, 'night_presence_window', {'enabled': False})
    gw.STATE.alerts.night_presence_active = False
    app_client.post('/api/arm', json={'state': 'home'}, headers=admin_headers)
    gw._check_night_presence()
    assert gw.STATE.alerts.night_presence_active is False
    app_client.post('/api/arm', json={'state': 'away'}, headers=admin_headers)
    gw._check_night_presence()
    assert gw.STATE.alerts.night_presence_active is True
    gw.STATE.alerts.night_presence_active = False
    app_client.post('/api/arm', json={'state': 'home'}, headers=admin_headers)
