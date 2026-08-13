"""Sessions must survive a restart of the process that issued them."""
import importlib
import json
import os
import stat

import pytest

from basic_pipelines import drishti_auth


@pytest.fixture
def store(tmp_path):
    """A fresh auth module bound to a file, reset afterwards."""
    path = str(tmp_path / "sessions.json")
    importlib.reload(drishti_auth)
    drishti_auth.configure(path)
    yield drishti_auth, path
    importlib.reload(drishti_auth)


def _restart(path):
    """What systemctl restart does: a new process, same file."""
    importlib.reload(drishti_auth)
    drishti_auth.configure(path)
    return drishti_auth


def test_a_session_survives_a_restart(store):
    auth, path = store
    token = auth.create_session("mani", "admin")

    auth = _restart(path)

    session = auth.get_session(token)
    assert session is not None, "signed out by a restart"
    assert session["username"] == "mani"
    assert session["role"] == "admin"


def test_signing_out_survives_a_restart_too(store):
    """A revoked token must not come back when the file is re-read."""
    auth, path = store
    token = auth.create_session("mani", "admin")
    auth.destroy_session(token)

    auth = _restart(path)

    assert auth.get_session(token) is None


def test_an_expired_session_is_not_restored(store):
    auth, path = store
    token = auth.create_session("mani", "admin", duration=-1)

    auth = _restart(path)

    assert auth.get_session(token) is None


def test_the_file_is_not_readable_by_other_accounts(store):
    auth, path = store
    auth.create_session("mani", "admin")
    mode = stat.S_IMODE(os.stat(path).st_mode)
    assert mode == 0o600, f"session tokens readable at {oct(mode)}"


def test_invalidating_a_user_survives_a_restart(store):
    """A password change must not be undone by the next restart."""
    auth, path = store
    mine = auth.create_session("mani", "admin")
    theirs = auth.create_session("someone", "user")
    auth.invalidate_user("mani")

    auth = _restart(path)

    assert auth.get_session(mine) is None
    assert auth.get_session(theirs) is not None


def test_a_corrupt_file_does_not_stop_the_app_starting(store, tmp_path):
    """A truncated write must cost the sessions, never the service."""
    auth, path = store
    open(path, "w").write("{ this is not json")

    auth = _restart(path)

    assert auth.get_session("anything") is None
    assert auth.create_session("mani", "admin")     # still usable


def test_without_a_path_it_stays_in_memory(tmp_path):
    """The default is unchanged, so nothing writes tokens unless asked to."""
    importlib.reload(drishti_auth)
    token = drishti_auth.create_session("mani", "admin")
    assert drishti_auth.get_session(token) is not None
    assert not any(p.name == "sessions.json" for p in tmp_path.iterdir())
    importlib.reload(drishti_auth)


def test_the_file_holds_no_password(store):
    auth, path = store
    auth.create_session("mani", "admin")
    body = json.load(open(path))
    assert list(body.values())[0].keys() == {"username", "role", "expires"}
