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
    monkeypatch.setattr(gw.STATE.auth, 'sessions', {})
    monkeypatch.setattr(gw.STATE.auth, 'refresh_tokens', {})
    monkeypatch.setattr(gw.STATE.auth, 'persisted_refresh', {})
    monkeypatch.setattr(gw, '_ws_clients', {})
    monkeypatch.setattr(gw.STATE.auth, 'rate_store', gw.defaultdict(list))
    monkeypatch.setattr(gw.STATE.auth, 'users', {'user': {'password': 'x', 'role': 'user'},
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
    monkeypatch.setattr(gw.STATE.camera, 'frame_buffer', b'\xff\xd8fake-jpeg\xff\xd9')
    monkeypatch.setattr(gw.STATE.camera, 'frame_seq', 7)
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


def test_state_socket_takes_the_token_from_a_header(ws_client):
    """The macOS app sends it as X-Garuda-Token, keeping it out of the address."""
    token = gw.create_session('user')
    with ws_client.websocket_connect('/ws', headers={'X-Garuda-Token': token}):
        deadline = time.time() + 2
        while not gw._ws_clients and time.time() < deadline:
            time.sleep(0.01)
        assert list(gw._ws_clients.values()) == [{'username': 'user', 'role': 'user'}]


def _frames_in_background(monkeypatch, stop):
    """A camera that publishes a new numbered frame every 10 ms."""
    import threading
    monkeypatch.setattr(gw.STATE.camera, 'frame_seq', 0)
    monkeypatch.setattr(gw.STATE.camera, 'frame_buffer', b'0')

    def run():
        n = 0
        while not stop.is_set():
            n += 1
            with gw.STATE.camera.frame_lock:
                gw.STATE.camera.frame_buffer = str(n).encode()
                gw.STATE.camera.frame_seq = n
            time.sleep(0.01)
    t = threading.Thread(target=run, daemon=True)
    t.start()
    return t


def test_acked_stream_skips_frames_instead_of_queuing_them(ws_client, monkeypatch):
    """With ?ack=1 at most two frames are unanswered: a slow viewer gets the
    newest frame next, not the backlog."""
    import threading
    stop = threading.Event()
    _frames_in_background(monkeypatch, stop)
    token = gw.create_session('user')
    try:
        with ws_client.websocket_connect(f'/ws/stream?token={token}&ack=1') as ws:
            ws.receive_bytes()
            second = int(ws.receive_bytes())
            time.sleep(0.4)                   # the viewer is slow; ~40 frames go by
            ws.send_text('a')
            third = int(ws.receive_bytes())
            assert third - second > 20
    finally:
        stop.set()


def test_audio_socket_refuses_a_caller_without_a_session(ws_client):
    with pytest.raises(WebSocketDisconnect):
        with ws_client.websocket_connect('/ws/audio'):
            pass


def _fake_mic_feed():
    import sys
    from garuda_core.audio_feed import AudioFeed, CHUNK_BYTES
    script = ("import sys,time\n"
              "for i in range(500):\n"
              f"    sys.stdout.buffer.write(bytes([i % 256, 0]) * {CHUNK_BYTES // 2}); sys.stdout.flush(); time.sleep(0.02)\n")
    return AudioFeed(command=lambda: [sys.executable, '-c', script])


def test_audio_socket_sends_the_format_then_pcm_chunks(ws_client, monkeypatch):
    from garuda_core.audio_feed import CHUNK_BYTES
    feed = _fake_mic_feed()
    monkeypatch.setattr(gw, 'AUDIO_FEED', feed)
    token = gw.create_session('user')
    try:
        with ws_client.websocket_connect(f'/ws/audio?token={token}') as ws:
            assert ws.receive_json() == {'format': 's16le', 'rate': 16000, 'channels': 1, 'chunk_ms': 20}
            for _ in range(3):
                assert len(ws.receive_bytes()) == CHUNK_BYTES
    finally:
        feed.stop()


def test_audio_socket_closes_when_there_is_no_capture(ws_client, monkeypatch):
    class NoMic:
        available = False
    monkeypatch.setattr(gw, 'AUDIO_FEED', NoMic())
    token = gw.create_session('user')
    with pytest.raises(WebSocketDisconnect) as closed:
        with ws_client.websocket_connect(f'/ws/audio?token={token}') as ws:
            ws.receive_text()
    assert closed.value.code == 4503


def test_audio_listener_keeps_only_the_newest_chunks():
    import asyncio
    from garuda_core.audio_feed import AudioFeed, LISTENER_QUEUE

    async def run():
        q = asyncio.Queue(maxsize=LISTENER_QUEUE)
        for i in range(LISTENER_QUEUE + 4):
            AudioFeed._offer(q, i)
        return [q.get_nowait() for _ in range(q.qsize())]
    assert asyncio.run(run()) == list(range(4, LISTENER_QUEUE + 4))
