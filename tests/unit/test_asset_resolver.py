from __future__ import annotations

from io import BytesIO
from pathlib import Path

import httpx
import pytest
from PIL import Image

from letsaigc.assets import AssetResolver
from letsaigc.errors import ValidationError


def test_local_asset_is_copied_hashed_and_source_is_unchanged(tmp_path: Path) -> None:
    source = tmp_path / "source.png"
    Image.new("RGB", (32, 24), "red").save(source)
    original = source.read_bytes()
    resolved = AssetResolver().resolve(str(source), tmp_path / "inputs")
    assert resolved.mime_type == "image/png"
    assert (resolved.width, resolved.height) == (32, 24)
    assert Path(resolved.local_path).read_bytes() == original
    assert Path(resolved.derived_path).is_file()
    assert len(resolved.derived_sha256) == 64
    assert source.read_bytes() == original
    assert resolved.safe_source == "local:source.png"


def test_false_mime_is_rejected(tmp_path: Path) -> None:
    source = tmp_path / "fake.jpg"
    Image.new("RGB", (8, 8), "blue").save(source, format="PNG")
    with pytest.raises(ValidationError, match="does not match"):
        AssetResolver().resolve(str(source), tmp_path / "inputs")


@pytest.mark.parametrize("extension", ["jpg", "webp"])
def test_jpeg_and_webp_are_supported(tmp_path: Path, extension: str) -> None:
    path = tmp_path / f"input.{extension}"
    Image.new("RGB", (8, 8), "red").save(path)
    asset = AssetResolver().resolve(str(path), tmp_path / "inputs")
    assert Image.open(asset.derived_path).size == (8, 8)


def test_private_url_is_rejected_before_http_client_is_created(tmp_path: Path) -> None:
    created = False

    def client_factory():
        nonlocal created
        created = True
        raise AssertionError("HTTP client must not be created")

    resolver = AssetResolver(client_factory=client_factory, dns_resolver=lambda host, port: ["127.0.0.1"])
    with pytest.raises(ValidationError, match="non-public"):
        resolver.resolve("https://example.test/secret.png?token=hidden", tmp_path / "inputs")
    assert created is False


def test_redirect_is_revalidated_before_second_request(tmp_path: Path) -> None:
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        return httpx.Response(302, headers={"location": "https://internal.test/image.png"})

    def dns(host: str, port: int) -> list[str]:
        return ["127.0.0.1"] if host == "internal.test" else ["93.184.216.34"]

    resolver = AssetResolver(
        client_factory=lambda: httpx.Client(transport=httpx.MockTransport(handler)),
        dns_resolver=dns,
    )
    with pytest.raises(ValidationError, match="non-public"):
        resolver.resolve("https://public.test/image.png?secret=value", tmp_path / "inputs")
    assert requests == ["https://93.184.216.34/image.png?secret=value"]


def test_connection_is_pinned_with_original_tls_sni_and_no_url_secret_in_record(tmp_path: Path) -> None:
    png = BytesIO()
    Image.new("RGB", (8, 8), "red").save(png, format="PNG")
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, content=png.getvalue(), headers={"content-type": "image/png"})

    resolver = AssetResolver(
        client_factory=lambda: httpx.Client(transport=httpx.MockTransport(handler)),
        dns_resolver=lambda host, port: ["93.184.216.34"],
    )
    asset = resolver.resolve("https://example.test/image.png?token=private#secret", tmp_path / "inputs")
    request = requests[0]
    assert request.url.host == "93.184.216.34"
    assert request.headers["host"] == "example.test"
    assert request.extensions["sni_hostname"] == "example.test"
    assert asset.safe_source == "https://example.test/image.png"
    assert "private" not in asset.model_dump_json()


def test_rebinding_private_second_dns_answer_is_blocked(tmp_path: Path) -> None:
    answers = iter([["93.184.216.34"], ["10.0.0.1"]])
    resolver = AssetResolver(
        client_factory=lambda: httpx.Client(transport=httpx.MockTransport(lambda request: pytest.fail("HTTP called"))),
        dns_resolver=lambda host, port: next(answers),
    )
    with pytest.raises(ValidationError, match="non-public"):
        resolver.resolve("https://public.test/image.png", tmp_path / "inputs")


def test_oversized_stream_fails_before_decoding(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("letsaigc.assets.resolver.MAX_BYTES", 16)
    resolver = AssetResolver(
        client_factory=lambda: httpx.Client(transport=httpx.MockTransport(
            lambda request: httpx.Response(200, content=b"x" * 17, headers={"content-type": "image/png"})
        )),
        dns_resolver=lambda host, port: ["93.184.216.34"],
    )
    with pytest.raises(ValidationError, match="25 MiB"):
        resolver.resolve("https://public.test/image.png", tmp_path / "inputs")
