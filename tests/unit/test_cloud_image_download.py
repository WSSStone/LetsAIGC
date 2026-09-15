"""Offline signed-result collection: no DNS, provider, or GPU traffic."""
import logging
import socket

import httpx
import pytest

from letsaigc.pipelines.ui_cloud import CloudImageResponseError, download_image_url


@pytest.fixture
def network(monkeypatch):
    calls = []
    def handler(_):
        return httpx.Response(200, content=b"image bytes")

    def dispatch(request):
        calls.append(request)
        return handler(request)

    def install(callback):
        nonlocal handler
        handler = callback

    monkeypatch.setattr("letsaigc.pipelines.ui_cloud._ImageResultResolver._resolve_addresses",
                        staticmethod(lambda *_: ["93.184.216.34"]))
    monkeypatch.setattr("letsaigc.pipelines.ui_cloud.httpx.HTTPTransport",
                        lambda **_: httpx.MockTransport(dispatch))
    # Fail loudly if any test accidentally escapes the mocked resolver.
    monkeypatch.setattr(socket, "getaddrinfo", lambda *_a, **_k: pytest.fail("real DNS"))
    return calls, install


def test_signed_download_redacts_logs_pins_ip_and_drops_credentials(network, monkeypatch, caplog):
    calls, install = network
    monkeypatch.setenv("HTTPS_PROXY", "http://SENSITIVE-proxy.invalid")
    monkeypatch.setenv("LLM_API_KEY", "SENSITIVE-api-key")
    caplog.set_level(logging.DEBUG)
    install(lambda _: httpx.Response(302, headers={"location": "/second?token=SENSITIVE",
                                                 "set-cookie": "secret=SENSITIVE; Path=/"})
            if len(calls) == 1 else httpx.Response(200, content=b"png"))
    trace = {"stages": []}
    assert download_image_url("https://cdn.test/first?token=SENSITIVE#SENSITIVE", trace) == b"png"
    assert trace["download"] == {"attempt": 1, "http_statuses": [302, 200], "bytes": 3}
    for req in calls:
        assert req.url.host == "93.184.216.34"
        assert req.headers["host"] == "cdn.test"
        assert req.extensions["sni_hostname"] == "cdn.test"
        assert req.url.fragment == ""
        assert not {"authorization", "proxy-authorization", "cookie"} & set(req.headers)
    assert "SENSITIVE" not in str(trace) + caplog.text


@pytest.mark.parametrize("url", ["http://cdn.test/a", "file:///a", "data:image/png,x",
                                "https://user:password@cdn.test/a", "https://cdn.test/\na",
                                "https://cdn.test/\\a", "https://cdn.test/" + "a" * 8192])
def test_invalid_url_never_downloaded(network, url):
    calls, _ = network
    with pytest.raises(CloudImageResponseError):
        download_image_url(url, {"stages": []})
    assert calls == []


@pytest.mark.parametrize("addresses", [["127.0.0.1"], ["::1"], ["169.254.169.254"],
                                       ["10.0.0.1"], ["93.184.216.34", "192.168.1.1"]])
def test_non_public_address_never_contacted(network, monkeypatch, addresses):
    monkeypatch.setattr("letsaigc.pipelines.ui_cloud._ImageResultResolver._resolve_addresses",
                        staticmethod(lambda *_: addresses))
    with pytest.raises(CloudImageResponseError, match="image_download_unsafe_url"):
        download_image_url("https://cdn.test/a", {"stages": []})
    assert network[0] == []


def test_redirect_to_private_address_is_blocked(network, monkeypatch):
    calls, install = network
    monkeypatch.setattr("letsaigc.pipelines.ui_cloud._ImageResultResolver._resolve_addresses",
                        staticmethod(lambda host, _: ["127.0.0.1"] if host == "private.test"
                                     else ["93.184.216.34"]))
    install(lambda _: httpx.Response(302, headers={"location": "https://private.test/a"}))
    with pytest.raises(CloudImageResponseError, match="image_download_unsafe_url"):
        download_image_url("https://cdn.test/a", {"stages": []})
    assert len(calls) == 1


@pytest.mark.parametrize("kind,code,count", [
    ("http", "image_download_http_error", 1), ("timeout", "image_download_timeout", 1),
    ("transport", "image_download_transport_error", 1),
    ("redirect", "image_download_redirect_limit", 4),
    ("location", "image_download_invalid_redirect", 1),
    ("declared_size", "image_download_too_large", 1), ("stream_size", "image_download_too_large", 1),
    ("encoding", "image_download_content_encoding", 1),
])
def test_download_failures_bounded_and_sanitized(network, monkeypatch, kind, code, count):
    calls, install = network
    monkeypatch.setattr("letsaigc.assets.resolver.MAX_BYTES", 16)

    def handler(req):
        if kind == "timeout":
            raise httpx.ReadTimeout("SENSITIVE URL", request=req)
        if kind == "transport":
            raise httpx.ConnectError("SENSITIVE URL", request=req)
        if kind == "http":
            return httpx.Response(403, text="SENSITIVE body")
        if kind == "redirect":
            return httpx.Response(302, headers={"location": "/a?token=SENSITIVE"})
        if kind == "location":
            return httpx.Response(302)
        if kind == "declared_size":
            return httpx.Response(200, headers={"content-length": "17"})
        if kind == "encoding":
            return httpx.Response(200, headers={"content-encoding": "gzip"})
        return httpx.Response(200, stream=httpx.ByteStream(b"x" * 17))

    install(handler)
    trace = {"stages": []}
    with pytest.raises(CloudImageResponseError, match=code) as failure:
        download_image_url("https://cdn.test/a?token=SENSITIVE", trace)
    assert len(calls) == count
    assert "SENSITIVE" not in str(trace) + str(failure.value)
