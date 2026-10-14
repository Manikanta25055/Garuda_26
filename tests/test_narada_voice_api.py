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


def test_a_voice_error_from_the_browser_is_logged_with_its_details(app_client, user_headers, monkeypatch):
    # (that it needs a session is held by tests/contract/routes.json)
    said = []
    monkeypatch.setattr(gw, "log_system_update", lambda m, *a, **k: said.append(m))
    body = {"message": "Server error: Unknown error", "type": "server_error", "code": "1011",
            "debug": "tts stream\nfailed", "state": "talking", "conversation": "conv_1"}
    assert app_client.post("/api/narada/voice/error", json=body, headers=user_headers).status_code == 200
    assert any("server_error" in m and "1011" in m and "tts stream failed" in m and "talking" in m
               for m in said), said


def test_voice_display_turns_require_the_bound_session(app_client, user_headers, admin_headers, monkeypatch):
    from types import SimpleNamespace
    from basic_pipelines.garuda_auto.narada_voice import NaradaVoice

    sdk = SimpleNamespace(conversational_ai=SimpleNamespace(conversations=SimpleNamespace(
        get_webrtc_token=lambda **kw: SimpleNamespace(token='tok', conversation_id='display-conv'))))
    voice = NaradaVoice('test', 'engine', client=sdk,
                        reply_fn=lambda *a: {'reply': 'One. Two.', 'display_reply': '1. One\n2. Two'})
    monkeypatch.setattr(gw, 'NARADA_VOICE', voice)
    assert app_client.post('/api/narada/voice/token', headers=user_headers).status_code == 200
    asyncio.run(voice.reply_for('display-conv', [SimpleNamespace(role='user', content='list')]))
    path = '/api/narada/voice/turns?conversation_id=display-conv'
    assert app_client.get(path, headers={'Cookie': ''}).status_code in (401, 403)
    assert app_client.get(path, headers=admin_headers).status_code == 404
    result = app_client.get(path, headers=user_headers)
    assert result.status_code == 200
    assert result.json()['turns'][0]['response'] == '1. One\n2. Two'
    assert app_client.get(path + '&after=1', headers=user_headers).json()['turns'] == []
    assert app_client.get(path + '&after=-1', headers=user_headers).status_code == 422


def test_the_question_heard_is_there_at_once_for_its_caller_only(app_client, user_headers, admin_headers,
                                                                 monkeypatch):
    # The page closes the microphone the moment a question is in, and shows it:
    # the speech service tells the browser ~3 s later.
    import time as _time
    from types import SimpleNamespace
    from basic_pipelines.garuda_auto.narada_voice import NaradaVoice

    sdk = SimpleNamespace(conversational_ai=SimpleNamespace(conversations=SimpleNamespace(
        get_webrtc_token=lambda **kw: SimpleNamespace(token='tok', conversation_id='heard-conv'))))

    def slow(*a, **k):
        _time.sleep(0.5)
        return {'reply': 'Quiet.'}
    voice = NaradaVoice('test', 'engine', client=sdk, reply_fn=slow)
    monkeypatch.setattr(gw, 'NARADA_VOICE', voice)
    assert app_client.post('/api/narada/voice/token', headers=user_headers).status_code == 200
    path = '/api/narada/voice/turns?conversation_id=heard-conv'
    assert app_client.get(path, headers=user_headers).json()['heard'] is None

    async def ask(text):
        gen = voice.speak('heard-conv', [SimpleNamespace(role='user', content=text)])
        first = asyncio.ensure_future(gen.__anext__())
        await asyncio.sleep(0.05)
        body = app_client.get(path, headers=user_headers).json()
        first.cancel()                      # what the SDK does when a call ends
        try:
            await first
        except (asyncio.CancelledError, StopAsyncIteration):
            pass
        await gen.aclose()
        return body
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(1) as pool:
        body = pool.submit(asyncio.run, ask('is the lamp on?')).result(timeout=10)
        assert body['heard'] == {'n': 1, 'text': 'is the lamp on?'} and body['turns'] == []
        pool.submit(asyncio.run, ask('...')).result(timeout=10)        # a noise is not a question
        assert app_client.get(path, headers=user_headers).json()['heard']['n'] == 1
    assert app_client.get(path, headers=admin_headers).status_code == 404
