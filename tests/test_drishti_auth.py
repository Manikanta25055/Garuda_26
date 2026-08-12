import pytest
from fastapi import HTTPException
from basic_pipelines import drishti_auth

pytestmark = pytest.mark.unit


class FakeRequest:
    def __init__(self, cookies=None):
        self.cookies = cookies or {}


def test_session_round_trips():
    token = drishti_auth.create_session("mani", "admin")
    session = drishti_auth.get_session(token)
    assert session["username"] == "mani"
    assert session["role"] == "admin"


def test_unknown_token_has_no_session():
    assert drishti_auth.get_session("nope") is None


def test_empty_token_has_no_session():
    assert drishti_auth.get_session("") is None
    assert drishti_auth.get_session(None) is None


def test_expired_session_is_not_returned():
    token = drishti_auth.create_session("mani", "user", duration=-1)
    assert drishti_auth.get_session(token) is None


def test_destroy_removes_the_session():
    token = drishti_auth.create_session("mani", "user")
    assert drishti_auth.destroy_session(token) is True
    assert drishti_auth.get_session(token) is None


def test_require_session_rejects_a_request_with_no_cookie():
    with pytest.raises(HTTPException) as exc:
        drishti_auth.require_drishti_session(FakeRequest())
    assert exc.value.status_code == 401


def test_require_session_accepts_a_valid_cookie():
    token = drishti_auth.create_session("mani", "user")
    request = FakeRequest({drishti_auth.COOKIE_NAME: token})
    assert drishti_auth.require_drishti_session(request)["username"] == "mani"


def test_require_admin_rejects_a_non_admin():
    token = drishti_auth.create_session("guest", "user")
    request = FakeRequest({drishti_auth.COOKIE_NAME: token})
    with pytest.raises(HTTPException) as exc:
        drishti_auth.require_drishti_admin(request)
    assert exc.value.status_code == 403


def test_require_admin_accepts_an_admin():
    token = drishti_auth.create_session("mani", "admin")
    request = FakeRequest({drishti_auth.COOKIE_NAME: token})
    assert drishti_auth.require_drishti_admin(request)["role"] == "admin"


def test_a_garuda_cookie_does_not_authenticate_drishti():
    """The cookie name is the boundary. A Garuda session must not carry over."""
    token = drishti_auth.create_session("mani", "admin")
    request = FakeRequest({"garuda_session": token})
    with pytest.raises(HTTPException):
        drishti_auth.require_drishti_session(request)


def test_cookie_is_not_named_like_garudas():
    assert drishti_auth.COOKIE_NAME == "drishti_session"
    assert drishti_auth.COOKIE_NAME != "garuda_session"


def test_prune_removes_only_expired_sessions():
    live = drishti_auth.create_session("a", "user")
    drishti_auth.create_session("b", "user", duration=-1)
    drishti_auth.prune_expired()
    assert drishti_auth.get_session(live) is not None


def test_invalidate_user_drops_every_session_for_that_account():
    first = drishti_auth.create_session("carol", "user")
    second = drishti_auth.create_session("carol", "user")
    other = drishti_auth.create_session("dave", "user")
    assert drishti_auth.invalidate_user("carol") == 2
    assert drishti_auth.get_session(first) is None
    assert drishti_auth.get_session(second) is None
    assert drishti_auth.get_session(other) is not None


def test_tokens_are_unguessable():
    tokens = {drishti_auth.create_session("mani", "user") for _ in range(20)}
    assert len(tokens) == 20
    assert all(len(t) >= 32 for t in tokens)
