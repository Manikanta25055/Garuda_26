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


# ── The runtime loop is actually connected (frontend rework) ──────────────────

@pytest.mark.integration
def test_the_runtime_exists_and_holds_a_live_descriptor():
    runtime = Garuda_web.DRISHTI_RUNTIME
    assert runtime is not None
    # Not the empty dict: the local lane answers state questions out of this,
    # and an empty one means every question gets "I have no reading yet".
    assert Garuda_web.DRISHTI_CTX.descriptor["occupancy"] in ("empty", "occupied")
    assert "person_count" in Garuda_web.DRISHTI_CTX.descriptor


@pytest.mark.integration
def test_every_seeded_device_contributes_a_field():
    descriptor = Garuda_web.DRISHTI_CTX.descriptor
    for device in Garuda_web.DRISHTI_CTX.registry.devices:
        assert f"{device['id']}_state" in descriptor


@pytest.mark.integration
def test_adding_a_device_rebinds_the_scene_without_a_restart():
    ctx = Garuda_web.DRISHTI_CTX
    assert ctx.on_registry_change is not None
    assert ctx.on_registry_change == Garuda_web.DRISHTI_RUNTIME.rebind


@pytest.mark.integration
def test_the_descriptor_is_throttled_below_frame_rate():
    # 30 fps into a 2 Hz rule loop would be 15 wasted rebuilds per tick.
    assert Garuda_web._DRISHTI_OBSERVE_INTERVAL_S >= 0.1


@pytest.mark.integration
def test_the_rule_thread_stops_with_the_app():
    import inspect
    source = inspect.getsource(Garuda_web._lifespan)
    assert "DRISHTI_RUNTIME.start()" in source
    assert "DRISHTI_RUNTIME.stop()" in source
