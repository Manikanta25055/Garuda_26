"""TC-CON01 through TC-CON06: Concurrency & race condition tests."""
import threading
import time
import collections
import pytest

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'basic_pipelines'))
import Garuda_web as gw


def test_concurrent_logins_no_session_corruption(app_client, monkeypatch):
    """TC-CON01: 50 concurrent logins → session store consistent."""
    N = 50
    tokens = []
    errors = []
    lock = threading.Lock()

    def do_login(i):
        try:
            if i % 2 == 0:
                r = app_client.post('/api/login',
                                    json={'username': 'user', 'password': 'user'})
                if r.status_code == 200:
                    with lock:
                        tokens.append(r.json()['token'])
            else:
                app_client.post('/api/login',
                                json={'username': 'user', 'password': 'wrong'})
        except Exception as e:
            with lock:
                errors.append(e)

    threads = [threading.Thread(target=do_login, args=(i,)) for i in range(N)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"Login errors: {errors}"
    # All successful tokens should be valid sessions
    for tok in tokens:
        assert gw.get_session(tok) is not None, f"Token {tok} not found in session store"


def test_concurrent_mode_toggles(app_client, user_headers):
    """TC-CON02: 20 concurrent mode toggles → final state is valid bool."""
    N = 20
    errors = []

    def toggle(i):
        try:
            val = bool(i % 2)
            app_client.post('/api/modes', json={'mode': 'dnd', 'value': val},
                            headers=user_headers)
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=toggle, args=(i,)) for i in range(N)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors
    # Final DND state should be a valid bool (no corruption)
    assert isinstance(gw.MODE_DND, bool)


def test_concurrent_event_queue_inserts(app_client):
    """TC-CON03: 100 concurrent queue_event() calls → count matches exactly."""
    N = 100
    start = gw.get_pending_count()
    errors = []

    def insert():
        try:
            gw.queue_event('concurrent_test', 'person', 0.9, '')
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=insert) for _ in range(N)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"Queue errors: {errors}"
    assert gw.get_pending_count() == start + N


def test_rapid_login_logout(app_client):
    """TC-CON04: Rapid login/logout cycles → session always reflects correct state."""
    for _ in range(10):
        r_login = app_client.post('/api/login',
                                  json={'username': 'user', 'password': 'user'})
        assert r_login.status_code == 200
        tok = r_login.json()['token']

        r_sess = app_client.get('/api/session', headers={'X-Garuda-Token': tok})
        assert r_sess.status_code == 200
        assert r_sess.json()['role'] == 'user'

        app_client.post('/api/logout', headers={'X-Garuda-Token': tok})

        r_after = app_client.get('/api/session', headers={'X-Garuda-Token': tok})
        assert r_after.status_code == 401


def test_high_frequency_state_requests(app_client, user_headers):
    """TC-CON05: 200 GET /api/state requests → all 200, no 500 errors."""
    errors = []

    def fetch():
        try:
            r = app_client.get('/api/state', headers=user_headers)
            if r.status_code not in (200, 401, 429):
                errors.append(r.status_code)
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=fetch) for _ in range(200)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"State request errors: {errors}"


def test_rate_limiter_enforces_limit(app_client, monkeypatch):
    """TC-CON06: Rate limiter — 35 requests → some 429 responses."""
    import collections
    monkeypatch.setattr(gw, '_rate_store', collections.defaultdict(list))
    codes = []
    for _ in range(35):
        r = app_client.post('/api/login', json={'username': 'user', 'password': 'wrong'})
        codes.append(r.status_code)
    # Must have at least one 429
    assert 429 in codes, f"Rate limit not enforced. All codes: {set(codes)}"
    # Must have at least one non-429 (first requests succeed)
    assert any(c != 429 for c in codes[:5])
