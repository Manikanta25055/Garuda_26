"""The microphone: the capture hub, /api/audio/level, /api/audio/clip and the
sound facts shortcuts test. A fake capture stands in for parec."""
import io
import sys
import wave

import pytest
from starlette.testclient import TestClient

import conftest  # noqa: F401
import Garuda_web as gw
from garuda_core import audio_feed as af


def _feed(amplitude):
    script = ("import sys,time\n"
              "for i in range(2000):\n"
              f"    sys.stdout.buffer.write(({amplitude}).to_bytes(2,'little',signed=True)*{af.CHUNK_BYTES // 2}); "
              "sys.stdout.flush(); time.sleep(0.02)\n")
    return af.AudioFeed(command=lambda: [sys.executable, '-c', script])


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(gw.STATE.auth, 'sessions', {})
    monkeypatch.setattr(gw.STATE.auth, 'rate_store', gw.defaultdict(list))
    monkeypatch.setattr(gw.STATE.auth, 'users', {'user': {'password': 'x', 'role': 'user'}})
    return TestClient(gw.fastapi_app)


@pytest.fixture
def loud_feed(monkeypatch):
    feed = _feed(16000)                    # about -6 dBFS
    monkeypatch.setattr(gw, 'AUDIO_FEED', feed)
    yield feed
    feed.stop()


def test_level_needs_a_session(client, loud_feed):
    assert client.get('/api/audio/level').status_code == 401


def test_level_reports_the_room(client, loud_feed):
    token = gw.create_session('user')
    r = client.get('/api/audio/level?seconds=1', headers={'X-Garuda-Token': token})
    assert r.status_code == 200
    body = r.json()
    assert body['sound'] == 'loud' and -7 < body['db'] < -5 and body['seconds'] >= 0.9


def test_clip_is_a_wav_of_the_asked_length(client, loud_feed):
    token = gw.create_session('user')
    r = client.get(f'/api/audio/clip?seconds=1&token={token}')
    assert r.status_code == 200 and r.headers['content-type'] == 'audio/wav'
    with wave.open(io.BytesIO(r.content)) as w:
        assert (w.getframerate(), w.getnchannels(), w.getnframes()) == (16000, 1, 16000)


def test_no_capture_is_a_503(client, monkeypatch):
    class NoMic:
        available = False
    monkeypatch.setattr(gw, 'AUDIO_FEED', NoMic())
    token = gw.create_session('user')
    assert client.get('/api/audio/level', headers={'X-Garuda-Token': token}).status_code == 503


def test_words_for_loudness():
    assert af.sound_word(None) == 'unknown'
    assert af.sound_word(-60) == 'quiet'
    assert af.sound_word(-30) == 'talking'
    assert af.sound_word(-10) == 'loud'


def test_sound_facts_are_unknown_while_nothing_listens(monkeypatch):
    feed = _feed(0)
    monkeypatch.setattr(gw, 'AUDIO_FEED', feed)
    facts = gw._shortcut_facts()
    assert facts['sound'] == 'unknown' and facts['sound_db'] == -100.0
    feed.stop()
