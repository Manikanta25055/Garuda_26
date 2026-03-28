"""TC-AD01 through TC-AD12: Admin endpoint tests."""
import pytest

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'basic_pipelines'))
import Garuda_web as gw


def test_list_users_admin(app_client, admin_headers):
    """TC-AD01: Admin can list users."""
    r = app_client.get('/api/users', headers=admin_headers)
    assert r.status_code == 200
    d = r.json()
    assert 'admin' in d
    assert 'user' in d


def test_list_users_user_role(app_client, user_headers):
    """TC-AD02: User role cannot list users → 403."""
    r = app_client.get('/api/users', headers=user_headers)
    assert r.status_code == 403


def test_list_users_no_auth(app_client):
    """TC-AD03: No auth → 401."""
    r = app_client.get('/api/users')
    assert r.status_code == 401


def test_add_user(app_client, admin_headers):
    """TC-AD04: Admin adds new user → 200, user appears in list."""
    r = app_client.post('/api/users/add',
                        json={'username': 'newuser', 'password': 'pass123', 'role': 'user'},
                        headers=admin_headers)
    assert r.status_code == 200
    users = app_client.get('/api/users', headers=admin_headers).json()
    assert 'newuser' in users


def test_add_duplicate_user(app_client, admin_headers):
    """TC-AD05: Adding duplicate username → 400."""
    app_client.post('/api/users/add',
                    json={'username': 'dupuser', 'password': 'x', 'role': 'user'},
                    headers=admin_headers)
    r = app_client.post('/api/users/add',
                        json={'username': 'dupuser', 'password': 'x', 'role': 'user'},
                        headers=admin_headers)
    assert r.status_code == 400


def test_delete_admin_account_forbidden(app_client, admin_headers):
    """TC-AD06: Cannot delete admin account."""
    r = app_client.post('/api/users/delete', json={'username': 'admin'}, headers=admin_headers)
    assert r.status_code in (400, 403)


def test_delete_user(app_client, admin_headers):
    """TC-AD07: Delete newly added user → 200, no longer in list."""
    app_client.post('/api/users/add',
                    json={'username': 'todelete', 'password': 'x', 'role': 'user'},
                    headers=admin_headers)
    r = app_client.post('/api/users/delete', json={'username': 'todelete'}, headers=admin_headers)
    assert r.status_code == 200
    users = app_client.get('/api/users', headers=admin_headers).json()
    assert 'todelete' not in users


def test_update_user_display_name(app_client, admin_headers):
    """TC-AD08: Update user display_name → 200."""
    r = app_client.post('/api/users/update',
                        json={'username': 'user', 'display_name': 'New Name'},
                        headers=admin_headers)
    assert r.status_code == 200
    users = app_client.get('/api/users', headers=admin_headers).json()
    assert users['user']['display_name'] == 'New Name'


def test_arp_admin_only(app_client, admin_headers, user_headers):
    """TC-AD09: GET /api/arp admin → 200; user → 403."""
    ra = app_client.get('/api/arp', headers=admin_headers)
    assert ra.status_code == 200
    ru = app_client.get('/api/arp', headers=user_headers)
    assert ru.status_code == 403


def test_devices_list(app_client, admin_headers):
    """TC-AD10: GET /api/devices → 200, list present."""
    r = app_client.get('/api/devices', headers=admin_headers)
    assert r.status_code == 200
    assert 'devices' in r.json()


def test_add_device(app_client, admin_headers):
    """TC-AD11: Add device → 200."""
    r = app_client.post('/api/devices/add',
                        json={'name': 'TestPhone', 'mac': 'aa:bb:cc:dd:ee:ff'},
                        headers=admin_headers)
    assert r.status_code == 200
    devs = app_client.get('/api/devices', headers=admin_headers).json()['devices']
    assert any(d['mac'] == 'aa:bb:cc:dd:ee:ff' for d in devs)


def test_delete_device(app_client, admin_headers):
    """TC-AD12: Delete device → 200, no longer in list."""
    app_client.post('/api/devices/add',
                    json={'name': 'TempPhone', 'mac': '11:22:33:44:55:66'},
                    headers=admin_headers)
    r = app_client.post('/api/devices/delete',
                        json={'mac': '11:22:33:44:55:66'},
                        headers=admin_headers)
    assert r.status_code == 200
    devs = app_client.get('/api/devices', headers=admin_headers).json()['devices']
    assert not any(d['mac'] == '11:22:33:44:55:66' for d in devs)
