"""The standalone Drishti site is halted; both hostnames get Garuda.

Both hostnames arrive on localhost:8080 through the same Cloudflare tunnel.
While DRISHTI_APP_ENABLED is off, the Host header no longer picks a bundle.
"""
import os

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.integration

Garuda_web = pytest.importorskip(
    "Garuda_web",
    reason="Garuda_web needs the GStreamer/Hailo stack",
)


@pytest.fixture
def client():
    return TestClient(Garuda_web.fastapi_app)


def test_the_standalone_app_is_halted_by_default():
    # Drishti's features moved into the Garuda app; the Svelte bundle and its
    # separate sign-in are off unless DRISHTI_APP_ENABLED=1.
    assert Garuda_web.DRISHTI_APP_ENABLED is False


def _is_svelte_bundle(html):
    return "/drishti/assets/" in html


def test_drishti_host_now_gets_the_merged_app(client):
    response = client.get("/", headers={"Host": Garuda_web.DRISHTI_HOST})
    assert response.status_code == 200
    assert not _is_svelte_bundle(response.text)
    assert "/static/app.js" in response.text


def test_other_hosts_still_get_the_merged_app(client):
    response = client.get("/", headers={"Host": "api.veeramanikanta.in"})
    assert response.status_code == 200
    assert not _is_svelte_bundle(response.text)


def test_the_bundle_is_not_mounted_while_halted(client):
    assert client.get("/drishti/manifest.webmanifest").status_code == 404


def test_re_enabling_serves_the_bundle_again(monkeypatch, client):
    if not (Garuda_web.DRISHTI_DIST / "index.html").is_file():
        pytest.skip("drishti_dist has not been built in this checkout")
    monkeypatch.setattr(Garuda_web, "DRISHTI_APP_ENABLED", True)
    assert _is_svelte_bundle(client.get("/", headers={"Host": Garuda_web.DRISHTI_HOST}).text)


def test_the_default_host_comes_from_the_environment():
    assert Garuda_web.DRISHTI_HOST == os.environ.get(
        "DRISHTI_HOST", "drishti.veeramanikanta.in")


def test_drishti_assets_do_not_spend_the_api_rate_budget():
    assert "/drishti/" in Garuda_web._RATE_EXEMPT_PREFIXES


# ── Garuda (security) and Drishti (home automation) by address ─────────────

def test_the_garuda_address_is_the_security_product(client):
    html = client.get("/", headers={"Host": "garuda.veeramanikanta.in"}).text
    assert 'data-product="security"' in html and "<title>Garuda</title>" in html


def test_other_addresses_are_the_home_product(client):
    for host in ("drishti.veeramanikanta.in", "api.veeramanikanta.in", "localhost:8080"):
        html = client.get("/", headers={"Host": host}).text
        assert 'data-product="home"' in html and "<title>Drishti</title>" in html, host


def test_home_automation_is_closed_on_the_security_address(client):
    garuda = client.get("/api/home/overview", headers={"Host": "garuda.veeramanikanta.in"})
    drishti = client.get("/api/home/overview", headers={"Host": "drishti.veeramanikanta.in"})
    assert garuda.status_code == 404
    assert drishti.status_code == 401        # exists, needs a session


def test_security_hosts_come_from_the_environment():
    assert Garuda_web._product_for_host("GARUDA.veeramanikanta.in:443") == "security"
    assert Garuda_web._product_for_host("notgaruda.veeramanikanta.in") == "home"


def test_each_product_has_its_own_icon(client):
    drishti = {"Host": "drishti.veeramanikanta.in"}
    garuda = {"Host": "garuda.veeramanikanta.in"}
    html = client.get("/", headers=drishti).text
    assert '"/static/drishti/favicon.png?v=' in html and '"/static/drishti/apple-touch-icon.png?v=' in html
    html = client.get("/", headers=garuda).text
    assert '"/static/favicon.png?v=' in html and "/static/drishti/" not in html
    for path in ("/favicon.ico", "/apple-touch-icon.png", "/apple-touch-icon-precomposed.png"):
        d, g = client.get(path, headers=drishti), client.get(path, headers=garuda)
        assert d.status_code == g.status_code == 200 and d.content != g.content, path
    assert client.get("/manifest.json", headers=drishti).json()["name"] == "Drishti"
    assert client.get("/manifest.json", headers=garuda).json()["short_name"] == "Garuda"
