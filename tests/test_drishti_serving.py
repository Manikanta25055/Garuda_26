"""The standalone Drishti site is halted; both hostnames get Garuda.

Both hostnames arrive on localhost:8080 through the same Cloudflare tunnel.
While DRISHTI_APP_ENABLED is off, the Host header no longer picks a bundle.
"""
import os

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.integration

Garuda_web = pytest.importorskip(
    "basic_pipelines.Garuda_web",
    reason="Garuda_web needs the GStreamer/Hailo stack",
)


@pytest.fixture
def client():
    return TestClient(Garuda_web.fastapi_app)


def test_the_standalone_app_is_halted_by_default():
    # Drishti's features moved into the Garuda app; the Svelte bundle and its
    # separate sign-in are off unless DRISHTI_APP_ENABLED=1.
    assert Garuda_web.DRISHTI_APP_ENABLED is False


def test_drishti_host_now_gets_garuda(client):
    response = client.get("/", headers={"Host": Garuda_web.DRISHTI_HOST})
    assert response.status_code == 200
    assert "Drishti</title>" not in response.text
    assert "GARUDA" in response.text


def test_other_hosts_still_get_garuda(client):
    response = client.get("/", headers={"Host": "api.veeramanikanta.in"})
    assert response.status_code == 200
    assert "Drishti</title>" not in response.text


def test_the_bundle_is_not_mounted_while_halted(client):
    assert client.get("/drishti/manifest.webmanifest").status_code == 404


def test_re_enabling_serves_the_bundle_again(monkeypatch, client):
    if not (Garuda_web.DRISHTI_DIST / "index.html").is_file():
        pytest.skip("drishti_dist has not been built in this checkout")
    monkeypatch.setattr(Garuda_web, "DRISHTI_APP_ENABLED", True)
    assert "Drishti" in client.get("/", headers={"Host": Garuda_web.DRISHTI_HOST}).text


def test_the_default_host_comes_from_the_environment():
    assert Garuda_web.DRISHTI_HOST == os.environ.get(
        "DRISHTI_HOST", "drishti.veeramanikanta.in")


def test_drishti_assets_do_not_spend_the_api_rate_budget():
    assert "/drishti/" in Garuda_web._RATE_EXEMPT_PREFIXES
