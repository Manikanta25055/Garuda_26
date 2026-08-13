"""The one app serves two sites, chosen by Host.

Both hostnames arrive on localhost:8080 through the same Cloudflare tunnel, so
nothing but the Host header distinguishes them.
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


def _dist_built():
    return (Garuda_web.DRISHTI_DIST / "index.html").is_file()


def test_drishti_host_gets_the_drishti_bundle(client):
    if not _dist_built():
        pytest.skip("drishti_dist has not been built in this checkout")
    response = client.get("/", headers={"Host": Garuda_web.DRISHTI_HOST})
    assert response.status_code == 200
    assert "Drishti" in response.text


def test_other_hosts_still_get_garuda(client):
    response = client.get("/", headers={"Host": "api.veeramanikanta.in"})
    assert response.status_code == 200
    assert "Drishti</title>" not in response.text


def test_a_port_on_the_drishti_host_still_matches(client):
    if not _dist_built():
        pytest.skip("drishti_dist has not been built in this checkout")
    response = client.get("/", headers={"Host": f"{Garuda_web.DRISHTI_HOST}:8080"})
    assert "Drishti" in response.text


def test_the_host_match_is_case_insensitive(client):
    if not _dist_built():
        pytest.skip("drishti_dist has not been built in this checkout")
    response = client.get("/", headers={"Host": Garuda_web.DRISHTI_HOST.upper()})
    assert "Drishti" in response.text


def test_a_lookalike_host_does_not_get_drishti(client):
    # Suffix matching would hand the app to anyone who can point
    # notdrishti.veeramanikanta.in at this tunnel.
    response = client.get("/", headers={"Host": f"not{Garuda_web.DRISHTI_HOST}"})
    assert "Drishti</title>" not in response.text


def test_drishti_assets_are_mounted(client):
    if not Garuda_web.DRISHTI_DIST.is_dir():
        pytest.skip("drishti_dist has not been built in this checkout")
    assert client.get("/drishti/manifest.webmanifest").status_code == 200


def test_drishti_host_is_configurable(monkeypatch, client):
    if not _dist_built():
        pytest.skip("drishti_dist has not been built in this checkout")
    monkeypatch.setattr(Garuda_web, "DRISHTI_HOST", "home.example.test")
    assert "Drishti" in client.get("/", headers={"Host": "home.example.test"}).text
    assert "Drishti</title>" not in client.get(
        "/", headers={"Host": "drishti.veeramanikanta.in"}).text


def test_the_default_host_comes_from_the_environment():
    assert Garuda_web.DRISHTI_HOST == os.environ.get(
        "DRISHTI_HOST", "drishti.veeramanikanta.in")


def test_drishti_assets_do_not_spend_the_api_rate_budget():
    assert "/drishti/" in Garuda_web._RATE_EXEMPT_PREFIXES
