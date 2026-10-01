"""The two browser sockets, driven for real (accept, auth, data), without the
service lifespan: the broadcaster task is not needed to check the handlers."""
import time

import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import conftest  # noqa: F401
import Garuda_web as gw


@pytest.fixture
def ws_client(monkeypatch):
    monkeypatch.setattr(gw, '_sessions', {})
    monkeypatch.setattr(gw, '_refresh_tokens', {})
    monkeypatch.setattr(gw, '_persisted_refresh', {})
    monkeypatch.setattr(gw, '_ws_clients', {})
    monkeypatch.setattr(gw, '_rate_store', gw.defaultdict(list))
    monkeypatch.setattr(gw, 'USERS', {'user': {'password': 'x', 'role': 'user'},
                                      'boss': {'password': 'x', 'role': 'admin'}})
    return TestClient(gw.fastapi_app)      # not entered: no lifespan, no background tasks


def test_state_socket_refuses_a_caller_without_a_session(ws_client):
    with pytest.raises(WebSocketDisconnect):
        with ws_client.websocket_connect('/ws'):
            pass
    assert gw._ws_clients == {}


def test_state_socket_registers_the_signed_in_user_and_forgets_them_on_close(ws_client):
    token = gw.create_session('boss')
    with ws_client.websocket_connect(f'/ws?token={token}'):
        deadline = time.time() + 2
        while not gw._ws_clients and time.time() < deadline:
            time.sleep(0.01)
        assert list(gw._ws_clients.values()) == [{'username': 'boss', 'role': 'admin'}]
    deadline = time.time() + 2
    while gw._ws_clients and time.time() < deadline:
        time.sleep(0.01)
    assert gw._ws_clients == {}


def test_stream_socket_sends_the_current_frame_to_a_signed_in_user(ws_client, monkeypatch):
    monkeypatch.setattr(gw, '_frame_buffer', b'\xff\xd8fake-jpeg\xff\xd9')
    monkeypatch.setattr(gw, '_frame_seq', 7)
    token = gw.create_session('user')
    with ws_client.websocket_connect(f'/ws/stream?token={token}') as ws:
        assert ws.receive_bytes() == b'\xff\xd8fake-jpeg\xff\xd9'


def test_stream_socket_refuses_a_caller_without_a_session(ws_client):
    with pytest.raises(WebSocketDisconnect):
        with ws_client.websocket_connect('/ws/stream'):
            pass


def test_socket_opens_are_limited_per_client(ws_client, monkeypatch):
    monkeypatch.setattr(gw, '_WS_CONNECT_LIMIT', 2)
    token = gw.create_session('user')
    for _ in range(2):
        with ws_client.websocket_connect(f'/ws?token={token}'):
            pass
    with pytest.raises(WebSocketDisconnect) as closed:
        with ws_client.websocket_connect(f'/ws?token={token}'):
            pass
    assert closed.value.code == 4029
