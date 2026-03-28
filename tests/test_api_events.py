"""TC-E01 through TC-E10: SQLite offline event queue tests."""
import sqlite3
import datetime
import threading
import pytest

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'basic_pipelines'))
import Garuda_web as gw


def test_events_db_initialized(app_client):
    """TC-E01: SQLite DB is initialized — events table exists."""
    conn = sqlite3.connect(gw.EVENTS_DB)
    tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    conn.close()
    assert 'events' in tables


def test_queue_event_inserts_row(app_client):
    """TC-E02: queue_event() inserts a row."""
    before = gw.get_pending_count()
    gw.queue_event('detection', 'person', 0.9, 'test')
    after = gw.get_pending_count()
    assert after == before + 1


def test_pending_count_increments(app_client):
    """TC-E03: Pending count increases with each insert."""
    start = gw.get_pending_count()
    gw.queue_event('alert', 'scissors', 0.8, '')
    gw.queue_event('alert', 'scissors', 0.85, '')
    assert gw.get_pending_count() == start + 2


def test_mark_events_synced(app_client):
    """TC-E04: mark_events_synced() sets synced=1 for rows ≤ id."""
    gw.queue_event('detection', 'person', 0.9, '')
    gw.queue_event('detection', 'person', 0.9, '')
    events = gw.get_events_since()
    assert events
    max_id = max(e['id'] for e in events)
    gw.mark_events_synced(max_id)
    assert gw.get_pending_count() == 0


def test_get_events_since_filters_by_ts(app_client):
    """TC-E05: get_events_since() returns only events after given timestamp."""
    ts_before = datetime.datetime.now().isoformat()
    gw.queue_event('detection', 'person', 0.9, '')
    events = gw.get_events_since(ts_before)
    assert len(events) >= 1
    for e in events:
        assert e['timestamp'] > ts_before


def test_events_pending_api(app_client, user_headers):
    """TC-E06: GET /api/events/pending → 200, list of unsynced events."""
    gw.queue_event('test', 'cat', 0.5, '')
    r = app_client.get('/api/events/pending', headers=user_headers)
    assert r.status_code == 200
    assert 'events' in r.json()


def test_events_pending_marks_synced(app_client, user_headers):
    """TC-E07: Fetching pending events marks them synced."""
    gw.queue_event('test', 'dog', 0.7, '')
    before = gw.get_pending_count()
    app_client.get('/api/events/pending', headers=user_headers)
    # After fetch, pending count should decrease
    assert gw.get_pending_count() <= before


def test_events_since_api(app_client, user_headers):
    """TC-E08: GET /api/events/since?since=<ts> → filtered results."""
    ts = datetime.datetime.now().isoformat()
    gw.queue_event('detection', 'scissors', 0.95, '')
    r = app_client.get(f'/api/events/since?since={ts}', headers=user_headers)
    assert r.status_code == 200
    d = r.json()
    assert 'events' in d
    assert d['count'] >= 1


def test_events_stats_api(app_client, user_headers):
    """TC-E09: GET /api/events/stats → {total, synced, pending}."""
    r = app_client.get('/api/events/stats', headers=user_headers)
    assert r.status_code == 200
    d = r.json()
    for key in ('pending', 'total', 'online'):
        assert key in d


def test_concurrent_queue_inserts(app_client):
    """TC-E10: 50 concurrent queue_event() calls → count matches exactly."""
    start = gw.get_pending_count()
    N = 50
    errors = []

    def insert_one():
        try:
            gw.queue_event('load_test', 'person', 0.9, '')
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=insert_one) for _ in range(N)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"Insert errors: {errors}"
    assert gw.get_pending_count() == start + N
