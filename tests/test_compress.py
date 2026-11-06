"""The page and its text files go out gzipped; streams and images do not.

Over the home network or Tailscale nothing compressed them: a phone fetched
style.css, app.js and the page (some 500 KB) byte for byte on every visit.
"""
import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.integration

Garuda_web = pytest.importorskip("Garuda_web", reason="Garuda_web needs the GStreamer/Hailo stack")

GZ = {"Accept-Encoding": "gzip"}


@pytest.fixture
def client():
    return TestClient(Garuda_web.fastapi_app)


@pytest.mark.parametrize("path", ["/static/style.css", "/static/app.js", "/static/icons/camera.svg"])
def test_text_files_are_gzipped_and_unchanged(client, path):
    raw = client.get(path, headers={"Accept-Encoding": "identity"})
    r = client.get(path, headers=GZ)
    assert r.status_code == 200 and r.headers["content-encoding"] == "gzip"
    assert int(r.headers["content-length"]) < len(raw.content) / 2
    assert r.content == raw.content                 # the client undoes the gzip
    assert "accept-encoding" in r.headers["vary"].lower()
    assert r.headers["etag"] == raw.headers["etag"]


def test_the_page_is_gzipped(client):
    r = client.get("/", headers=GZ)
    assert r.status_code == 200 and r.headers["content-encoding"] == "gzip"
    assert r.headers["cache-control"] == "no-cache"
    assert "/static/app.js" in r.text


def test_revalidation_still_answers_304(client):
    etag = client.get("/static/style.css", headers=GZ).headers["etag"]
    r = client.get("/static/style.css", headers={**GZ, "If-None-Match": etag})
    assert r.status_code == 304 and r.content == b""


def test_images_ranges_and_plain_clients_are_left_alone(client):
    assert "content-encoding" not in client.get("/static/icon-192.png", headers=GZ).headers
    r = client.get("/static/style.css", headers={**GZ, "Range": "bytes=0-99"})
    assert r.status_code == 206 and "content-encoding" not in r.headers and len(r.content) == 100
    assert "content-encoding" not in client.get("/static/style.css", headers={"Accept-Encoding": "identity"}).headers


def test_gzip_body_honours_q_zero():
    from basic_pipelines.garuda_core.compress import accepts_gzip
    assert accepts_gzip({"accept-encoding": "br, gzip, deflate"})
    assert not accepts_gzip({"accept-encoding": "gzip;q=0, identity"})
    assert not accepts_gzip({})
