"""
Input validation tests — exhaustive boundary and edge-case testing for all
validated fields across the API: usernames, passwords, emails, cooldown, labels,
commands, and Unicode/special character handling.
"""
import pytest
import sys, os
sys.path.insert(0, os.path.dirname(__file__))
import basic_pipelines.Garuda_web as gw


# ── Helper: get admin headers ────────────────────────────────────────────────

@pytest.fixture
def ah(admin_token):
    return {'X-Garuda-Token': admin_token}


# ── _validate_password_strength unit tests ────────────────────────────────────

class TestValidatePasswordStrength:
    def test_empty_string(self):
        assert gw._validate_password_strength('') is not None

    def test_whitespace_only(self):
        assert gw._validate_password_strength('        ') is not None

    def test_too_short(self):
        assert gw._validate_password_strength('Ab1') is not None

    def test_exactly_8_chars_valid(self):
        assert gw._validate_password_strength('Abcdef1g') is None

    def test_max_length_256(self):
        assert gw._validate_password_strength('Aa1' + 'x' * 253) is None

    def test_exceeds_256(self):
        assert gw._validate_password_strength('Aa1' + 'x' * 254) is not None

    def test_missing_uppercase(self):
        assert gw._validate_password_strength('abcdefg1') is not None

    def test_missing_lowercase(self):
        assert gw._validate_password_strength('ABCDEFG1') is not None

    def test_missing_digit(self):
        assert gw._validate_password_strength('Abcdefgh') is not None

    def test_all_rules_satisfied(self):
        assert gw._validate_password_strength('ValidPass1') is None

    def test_unicode_counts_for_rules(self):
        # Unicode uppercase satisfies uppercase requirement
        assert gw._validate_password_strength('CAFÉ1café') is None

    def test_returns_string_message_on_failure(self):
        err = gw._validate_password_strength('weak')
        assert isinstance(err, str) and len(err) > 0


# ── Username validation via add_user ─────────────────────────────────────────

class TestUsernameValidation:
    VALID_PW = 'ValidPass1'

    @pytest.mark.parametrize("username", ['ab', 'a', ''])
    def test_too_short(self, app_client, ah, username):
        r = app_client.post('/api/users/add',
                            json={'username': username, 'password': self.VALID_PW, 'role': 'user'},
                            headers=ah)
        assert r.status_code == 400, f"Expected 400 for username={username!r}"

    def test_exactly_3_chars(self, app_client, ah):
        r = app_client.post('/api/users/add',
                            json={'username': 'abc', 'password': self.VALID_PW, 'role': 'user'},
                            headers=ah)
        assert r.status_code == 200

    def test_exactly_32_chars(self, app_client, ah):
        r = app_client.post('/api/users/add',
                            json={'username': 'a' * 32, 'password': self.VALID_PW, 'role': 'user'},
                            headers=ah)
        assert r.status_code == 200

    def test_33_chars_rejected(self, app_client, ah):
        r = app_client.post('/api/users/add',
                            json={'username': 'a' * 33, 'password': self.VALID_PW, 'role': 'user'},
                            headers=ah)
        assert r.status_code == 400

    @pytest.mark.parametrize("bad_char", [' ', '@', '.', '!', '#', '/', '\\', '(', ')'])
    def test_invalid_characters(self, app_client, ah, bad_char):
        r = app_client.post('/api/users/add',
                            json={'username': f'user{bad_char}name',
                                  'password': self.VALID_PW, 'role': 'user'},
                            headers=ah)
        assert r.status_code == 400, f"Expected 400 for bad_char={bad_char!r}"

    @pytest.mark.parametrize("good_char", ['_', '-'])
    def test_allowed_special_chars(self, app_client, ah, good_char):
        r = app_client.post('/api/users/add',
                            json={'username': f'user{good_char}name',
                                  'password': self.VALID_PW, 'role': 'user'},
                            headers=ah)
        assert r.status_code == 200, f"Expected 200 for good_char={good_char!r}"

    def test_duplicate_username_rejected(self, app_client, ah):
        r = app_client.post('/api/users/add',
                            json={'username': 'user', 'password': self.VALID_PW, 'role': 'user'},
                            headers=ah)
        assert r.status_code == 400

    def test_sql_injection_in_username(self, app_client, ah):
        r = app_client.post('/api/users/add',
                            json={'username': "'; DROP TABLE users; --",
                                  'password': self.VALID_PW, 'role': 'user'},
                            headers=ah)
        assert r.status_code == 400


# ── Email recipient validation ────────────────────────────────────────────────

class TestEmailRecipientValidation:

    @pytest.mark.parametrize("bad_email", [
        'notanemail',
        'missing@domain',
        '@nodomain.com',
        'space in@email.com',
        '',
        'double@@at.com',
    ])
    def test_invalid_email_format_rejected(self, app_client, ah, bad_email):
        r = app_client.post('/api/config',
                            json={'email_recipients': [bad_email]},
                            headers=ah)
        assert r.status_code == 400, f"Expected 400 for email={bad_email!r}"

    @pytest.mark.parametrize("good_email", [
        'user@example.com',
        'user+tag@sub.domain.org',
        'a@b.io',
    ])
    def test_valid_email_format_accepted(self, app_client, ah, good_email):
        r = app_client.post('/api/config',
                            json={'email_recipients': [good_email]},
                            headers=ah)
        assert r.status_code == 200, f"Expected 200 for email={good_email!r}"

    def test_exactly_10_recipients_accepted(self, app_client, ah):
        recipients = [f'u{i}@example.com' for i in range(10)]
        r = app_client.post('/api/config', json={'email_recipients': recipients}, headers=ah)
        assert r.status_code == 200

    def test_11_recipients_rejected(self, app_client, ah):
        recipients = [f'u{i}@example.com' for i in range(11)]
        r = app_client.post('/api/config', json={'email_recipients': recipients}, headers=ah)
        assert r.status_code == 400

    def test_empty_list_accepted(self, app_client, ah):
        r = app_client.post('/api/config', json={'email_recipients': []}, headers=ah)
        assert r.status_code == 200


# ── Cooldown range validation ─────────────────────────────────────────────────

class TestEmailCooldownValidation:

    @pytest.mark.parametrize("val,ok", [
        (0,    False),
        (4,    False),
        (5,    True),
        (60,   True),
        (3600, True),
        (3601, False),
        (9999, False),
    ])
    def test_cooldown_boundary(self, app_client, ah, val, ok):
        r = app_client.post('/api/config', json={'email_cooldown': val}, headers=ah)
        expected = 200 if ok else 400
        assert r.status_code == expected, f"cooldown={val}: {r.text}"


# ── Custom command phrase/response limits ─────────────────────────────────────

class TestCustomCommandValidation:

    def test_phrase_at_200_chars_accepted(self, app_client, ah):
        r = app_client.post('/api/config/command/add',
                            json={'phrase': 'x' * 200, 'response': 'ok'},
                            headers=ah)
        assert r.status_code == 200

    def test_phrase_at_201_chars_rejected(self, app_client, ah):
        r = app_client.post('/api/config/command/add',
                            json={'phrase': 'x' * 201, 'response': 'ok'},
                            headers=ah)
        assert r.status_code == 400

    def test_response_at_500_chars_accepted(self, app_client, ah):
        r = app_client.post('/api/config/command/add',
                            json={'phrase': 'valid phrase', 'response': 'r' * 500},
                            headers=ah)
        assert r.status_code == 200

    def test_response_at_501_chars_rejected(self, app_client, ah):
        r = app_client.post('/api/config/command/add',
                            json={'phrase': 'another phrase', 'response': 'r' * 501},
                            headers=ah)
        assert r.status_code == 400

    def test_empty_phrase_rejected(self, app_client, ah):
        r = app_client.post('/api/config/command/add',
                            json={'phrase': '', 'response': 'ok'},
                            headers=ah)
        assert r.status_code == 400

    def test_whitespace_phrase_rejected(self, app_client, ah):
        r = app_client.post('/api/config/command/add',
                            json={'phrase': '   ', 'response': 'ok'},
                            headers=ah)
        assert r.status_code == 400

    def test_unicode_phrase_accepted(self, app_client, ah):
        r = app_client.post('/api/config/command/add',
                            json={'phrase': 'turn on नाइट mode', 'response': 'Night mode on'},
                            headers=ah)
        assert r.status_code == 200

    def test_xss_payload_in_phrase_stored_as_is(self, app_client, ah):
        """XSS in command phrase stored verbatim — frontend escapes on display."""
        payload = '<script>alert(1)</script>'
        r = app_client.post('/api/config/command/add',
                            json={'phrase': payload, 'response': 'ok'},
                            headers=ah)
        assert r.status_code == 200
        cfg = app_client.get('/api/config', headers=ah).json()
        assert payload.lower() in cfg['custom_voice_commands']


# ── Detection threshold ───────────────────────────────────────────────────────

class TestDetectionThreshold:

    @pytest.mark.parametrize("val,expected", [
        (-1.0,  0.05),   # clamped to min
        (0.0,   0.05),   # clamped to min
        (0.05,  0.05),   # exactly min
        (0.5,   0.5),    # middle
        (0.95,  0.95),   # exactly max
        (1.0,   0.95),   # clamped to max
        (99.9,  0.95),   # clamped to max
    ])
    def test_threshold_clamping(self, app_client, ah, val, expected):
        r = app_client.post('/api/config', json={'detection_threshold': val}, headers=ah)
        assert r.status_code == 200
        cfg = app_client.get('/api/config', headers=ah).json()
        assert abs(cfg['detection_threshold'] - expected) < 1e-9, \
            f"val={val}: got {cfg['detection_threshold']}, expected {expected}"


# ── Danger label + watch labels ───────────────────────────────────────────────

class TestLabelValidation:

    def test_empty_danger_label_not_overwritten(self, app_client, ah):
        """Sending empty danger_label must not clear the existing value."""
        app_client.post('/api/config', json={'danger_label': 'knife'}, headers=ah)
        app_client.post('/api/config', json={'danger_label': ''}, headers=ah)
        cfg = app_client.get('/api/config', headers=ah).json()
        assert cfg['danger_label'] == 'knife'

    def test_watch_labels_strips_whitespace(self, app_client, ah):
        r = app_client.post('/api/config',
                            json={'watch_labels': [' cat ', 'dog  ', '  bird']},
                            headers=ah)
        assert r.status_code == 200
        cfg = app_client.get('/api/config', headers=ah).json()
        assert cfg['watch_labels'] == ['cat', 'dog', 'bird']

    def test_watch_labels_filters_empty_strings(self, app_client, ah):
        r = app_client.post('/api/config',
                            json={'watch_labels': ['cat', '', '  ', 'dog']},
                            headers=ah)
        assert r.status_code == 200
        cfg = app_client.get('/api/config', headers=ah).json()
        assert '' not in cfg['watch_labels']
        assert '  ' not in cfg['watch_labels']


# ── Chat message validation ───────────────────────────────────────────────────

class TestChatValidation:

    def test_empty_message_rejected(self, app_client, user_token):
        headers = {'X-Garuda-Token': user_token}
        r = app_client.post('/api/chat', json={'message': ''}, headers=headers)
        assert r.status_code == 400

    def test_whitespace_only_message_rejected(self, app_client, user_token):
        headers = {'X-Garuda-Token': user_token}
        r = app_client.post('/api/chat', json={'message': '   '}, headers=headers)
        assert r.status_code == 400

    def test_valid_message_accepted(self, app_client, user_token):
        headers = {'X-Garuda-Token': user_token}
        r = app_client.post('/api/chat', json={'message': 'what is the status?'}, headers=headers)
        # 200 or 503 (no Groq key) — both OK; just must not be 400
        assert r.status_code != 400


# ── Auth edge cases ───────────────────────────────────────────────────────────

class TestAuthEdgeCases:

    def test_login_empty_username(self, app_client):
        r = app_client.post('/api/login', json={'username': '', 'password': 'user'})
        assert r.status_code in (400, 401)

    def test_login_empty_password(self, app_client):
        r = app_client.post('/api/login', json={'username': 'user', 'password': ''})
        assert r.status_code in (400, 401)

    def test_login_null_fields(self, app_client):
        r = app_client.post('/api/login', json={})
        assert r.status_code in (400, 422)

    def test_login_extra_fields_ignored(self, app_client):
        r = app_client.post('/api/login',
                            json={'username': 'user', 'password': 'user', 'evil': 'payload'})
        assert r.status_code == 200

    def test_session_with_garbage_token(self, app_client):
        r = app_client.get('/api/session', headers={'X-Garuda-Token': 'garbage!@#$'})
        assert r.status_code == 401

    def test_session_with_empty_token(self, app_client):
        r = app_client.get('/api/session', headers={'X-Garuda-Token': ''})
        assert r.status_code == 401
