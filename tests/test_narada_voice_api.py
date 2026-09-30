"""The voice token endpoint and the Speech Engine WebSocket."""
import asyncio

import Garuda_web as gw


def test_token_needs_a_session(app_client):
    assert app_client.post('/api/narada/voice/token').status_code in (401, 403)


def test_token_503_until_voice_is_set_up(app_client, user_headers, monkeypatch):
    monkeypatch.setattr(gw.NARADA_VOICE, 'engine_id', '')
    r = app_client.post('/api/narada/voice/token', headers=user_headers)
    assert r.status_code == 503 and 'setup_narada_voice' in r.json()['detail']


def test_token_is_bound_to_the_signed_in_user(app_client, user_headers, monkeypatch):
    seen = []
    monkeypatch.setattr(gw.NARADA_VOICE, 'api_key', 'sk_test')
    monkeypatch.setattr(gw.NARADA_VOICE, 'engine_id', 'seng_1')
    monkeypatch.setattr(gw.NARADA_VOICE, 'issue_token',
                        lambda user, role, scope: seen.append((user, role, scope)) or
                        {'token': 't', 'conversation_id': 'c'})
    r = app_client.post('/api/narada/voice/token', headers=user_headers)
    assert r.status_code == 200 and r.json() == {'token': 't', 'conversation_id': 'c'}
    assert len(seen) == 1 and seen[0][1] == 'user'


def test_speech_engine_socket_rejects_unsigned_connections():
    class FakeSocket:
        headers = {}
        closed = accepted = None

        async def close(self, code=1000):
            self.closed = code

        async def accept(self):
            self.accepted = True
    ws = FakeSocket()
    asyncio.run(gw.narada_voice_ws(ws))
    assert ws.closed == 1008 and ws.accepted is None
