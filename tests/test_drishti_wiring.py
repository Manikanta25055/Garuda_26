"""Task 14: the Drishti router is actually mounted on the running application.

Every other Drishti test builds its own context over a temp directory. These
tests are the only ones that touch the real module-level wiring, so they are
the only ones that would catch a router that was written but never included.
"""
import pytest

Garuda_web = pytest.importorskip(
    "basic_pipelines.Garuda_web",
    reason="Garuda_web needs the GStreamer/Hailo stack",
)


def _paths():
    return {r.path for r in Garuda_web.fastapi_app.routes if hasattr(r, "path")}


@pytest.mark.integration
@pytest.mark.parametrize("path", [
    "/api/drishti/login",
    "/api/drishti/logout",
    "/api/drishti/instruct",
    "/api/drishti/devices",
    "/api/drishti/device-types",
    "/api/drishti/proposals",
    "/api/drishti/rules",
    "/api/drishti/activity",
])
def test_drishti_route_is_mounted(path):
    assert path in _paths()


@pytest.mark.integration
def test_context_exists_and_is_wired():
    ctx = Garuda_web.DRISHTI_CTX
    assert ctx.registry is not None
    assert ctx.store is not None
    assert ctx.pending is not None
    assert ctx.device_router is not None
    assert ctx.schema is not None


@pytest.mark.integration
def test_every_relay_channel_has_a_pin():
    assert set(Garuda_web.RELAY_CHANNELS) <= set(Garuda_web.CHANNEL_TO_PIN)


@pytest.mark.integration
def test_no_two_channels_share_a_pin():
    pins = [Garuda_web.CHANNEL_TO_PIN[c] for c in Garuda_web.RELAY_CHANNELS]
    assert len(pins) == len(set(pins))


@pytest.mark.integration
def test_relay_pins_avoid_the_stepper():
    # The gesture prototype drives a stepper on 5/6/13/19 (moved there
    # precisely so it stops colliding with the relay bank).
    stepper = {5, 6, 13, 19}
    used = {Garuda_web.CHANNEL_TO_PIN[c] for c in Garuda_web.RELAY_CHANNELS}
    assert used.isdisjoint(stepper)


@pytest.mark.integration
def test_garuda_routes_still_exist():
    # The Drishti router must not have shadowed anything Garuda serves.
    paths = _paths()
    assert "/api/login" in paths
    assert "/static" in paths


@pytest.mark.integration
def test_constants_are_not_re_hardcoded():
    # A seeding script reads these from drishti_config precisely so it does not
    # have to import this module. They must stay the same object.
    from basic_pipelines import drishti_config
    assert Garuda_web.RELAY_CHANNELS is drishti_config.RELAY_CHANNELS
    assert Garuda_web.CHANNEL_TO_PIN is drishti_config.CHANNEL_TO_PIN
    assert Garuda_web.DRISHTI_DATA_DIR == drishti_config.DATA_DIR
