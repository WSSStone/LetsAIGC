from __future__ import annotations

import hashlib
import ipaddress
import socket
import time
import uuid
from collections.abc import Callable
from io import BytesIO
from pathlib import Path
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx
from PIL import Image, ImageOps, UnidentifiedImageError

from ..errors import ValidationError
from ..schemas import ResolvedAsset

MAX_BYTES = 25 * 1024 * 1024
MAX_PIXELS = 64 * 1024 * 1024
ALLOWED_MIME = {"image/png": "PNG", "image/jpeg": "JPEG", "image/webp": "WEBP"}
FORMAT_EXTENSIONS = {"PNG": ".png", "JPEG": ".jpg", "WEBP": ".webp"}


class AssetResolver:
    def __init__(
        self,
        *,
        timeout: float = 30.0,
        max_redirects: int = 3,
        client_factory: Callable[[], httpx.Client] | None = None,
        dns_resolver: Callable[[str, int], list[str]] | None = None,
    ) -> None:
        self.timeout = timeout
        self.max_redirects = max_redirects
        self.client_factory = client_factory or (
            lambda: httpx.Client(timeout=httpx.Timeout(timeout), follow_redirects=False, trust_env=False)
        )
        self.dns_resolver = dns_resolver or self._resolve_addresses

    def resolve_many(self, sources: list[str], destination: Path) -> list[ResolvedAsset]:
        if len(sources) > 8:
            raise ValidationError("At most 8 input images are accepted per Agent task")
        destination.mkdir(parents=True, exist_ok=True)
        return [self.resolve(source, destination) for source in sources]

    def resolve(self, source: str, destination: Path) -> ResolvedAsset:
        destination.mkdir(parents=True, exist_ok=True)
        lowered = source.lower()
        if lowered.startswith("https://"):
            try:
                content, mime_type, safe_source = self._fetch_https(source)
            except (httpx.HTTPError, ValueError) as exc:
                raise ValidationError("Image HTTPS request failed; URL credentials were omitted") from exc
            source_type = "https"
        else:
            if "://" in source or lowered.startswith(("http:", "file:")):
                raise ValidationError("Only HTTPS image URLs are accepted")
            path = Path(source).expanduser()
            if not path.exists() or not path.is_file():
                raise ValidationError(f"Input image is not a regular file: {source}")
            if path.stat().st_size > MAX_BYTES:
                raise ValidationError("Input image exceeds 25 MiB")
            content = path.read_bytes()
            mime_type = {
                ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp",
            }.get(path.suffix.lower(), "application/octet-stream")
            safe_source = f"local:{path.name}"
            source_type = "local"
        image_format, width, height, canonical_mime = self._validate_image(content, mime_type)
        digest = hashlib.sha256(content).hexdigest()
        target = destination / f"original-{digest}{FORMAT_EXTENSIONS[image_format]}"
        if not target.exists():
            temporary = destination / f".{uuid.uuid4().hex}.tmp"
            temporary.write_bytes(content)
            temporary.replace(target)
        derived = destination / f"derived-{digest}.png"
        if not derived.exists():
            with Image.open(BytesIO(content)) as image:
                alpha = "A" in image.getbands() or "transparency" in image.info
                controlled = ImageOps.exif_transpose(image).convert("RGBA" if alpha else "RGB")
                controlled.thumbnail((1536, 1536), Image.Resampling.LANCZOS)
                controlled.info.clear()
                controlled.save(derived, format="PNG", optimize=True)
        derived_digest = hashlib.sha256(derived.read_bytes()).hexdigest()
        return ResolvedAsset(
            id=f"asset-{digest[:16]}",
            source_type=source_type,
            safe_source=safe_source,
            mime_type=canonical_mime,
            width=width,
            height=height,
            size_bytes=len(content),
            local_path=str(target.resolve()),
            sha256=digest,
            derived_path=str(derived.resolve()),
            derived_sha256=derived_digest,
        )

    def _fetch_https(self, source: str) -> tuple[bytes, str, str]:
        current = source
        deadline = time.monotonic() + self.timeout
        self._validate_public_url(current)
        with self.client_factory() as client:
            for hop in range(self.max_redirects + 1):
                parsed, address = self._validate_public_url(current)
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ValidationError("Image download exceeded its time budget")
                # Connect to the validated IP, not a second DNS result. Retain the
                # original Host and TLS SNI/certificate hostname, including IPv6.
                pinned = httpx.URL(current).copy_with(host=address, fragment=None)
                with client.stream(
                    "GET", pinned,
                    headers={"Host": parsed.netloc, "Accept-Encoding": "identity"},
                    extensions={"sni_hostname": parsed.hostname},
                    timeout=remaining,
                ) as response:
                    if response.status_code in {301, 302, 303, 307, 308}:
                        if hop >= self.max_redirects:
                            raise ValidationError("Image URL exceeded 3 redirects")
                        location = response.headers.get("location")
                        if not location:
                            raise ValidationError("Image redirect did not include a location")
                        current = urljoin(current, location)
                        continue
                    try:
                        response.raise_for_status()
                    except httpx.HTTPError as exc:
                        raise ValidationError(
                            "Image URL request failed", details={"status": response.status_code}
                        ) from exc
                    declared_length = response.headers.get("content-length")
                    if declared_length and int(declared_length) > MAX_BYTES:
                        raise ValidationError("Remote image exceeds 25 MiB")
                    content = bytearray()
                    for chunk in response.iter_bytes():
                        if time.monotonic() > deadline:
                            raise ValidationError("Image download exceeded its time budget")
                        content.extend(chunk)
                        if len(content) > MAX_BYTES:
                            raise ValidationError("Remote image exceeds 25 MiB")
                    mime = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                    safe_source = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))
                    return bytes(content), mime, safe_source
        raise ValidationError("Image URL could not be resolved")

    def _validate_public_url(self, source: str):
        parsed = urlsplit(source)
        if parsed.scheme.lower() != "https" or not parsed.hostname:
            raise ValidationError("Only absolute HTTPS image URLs are accepted")
        if parsed.username or parsed.password:
            raise ValidationError("Credentials in image URLs are not accepted")
        port = parsed.port or 443
        addresses = self.dns_resolver(parsed.hostname, port)
        if not addresses:
            raise ValidationError("Image URL hostname did not resolve")
        for address in addresses:
            ip = ipaddress.ip_address(address)
            if not ip.is_global or ip.is_multicast or ip.is_reserved:
                raise ValidationError("Image URL resolved to a non-public address")
        return parsed, addresses[0]

    @staticmethod
    def _resolve_addresses(host: str, port: int) -> list[str]:
        try:
            return sorted({item[4][0] for item in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)})
        except socket.gaierror as exc:
            raise ValidationError("Image URL hostname resolution failed") from exc

    @staticmethod
    def _validate_image(content: bytes, declared_mime: str) -> tuple[str, int, int, str]:
        if not content or len(content) > MAX_BYTES:
            raise ValidationError("Input image is empty or exceeds 25 MiB")
        if declared_mime not in ALLOWED_MIME:
            raise ValidationError(f"Unsupported image MIME type: {declared_mime or 'missing'}")
        try:
            with Image.open(BytesIO(content)) as image:
                width, height = image.size
                if width <= 0 or height <= 0 or width * height > MAX_PIXELS or getattr(image, "is_animated", False):
                    raise ValidationError("Input image dimensions or animation are unsafe")
                image.verify()
            with Image.open(BytesIO(content)) as image:
                image.load()
                image_format = str(image.format).upper()
                width, height = image.size
        except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
            raise ValidationError("Input image could not be safely decoded") from exc
        if image_format != ALLOWED_MIME[declared_mime]:
            raise ValidationError("Image MIME type does not match decoded format")
        if width <= 0 or height <= 0 or width * height > MAX_PIXELS:
            raise ValidationError("Input image dimensions are unsafe")
        canonical = "image/jpeg" if image_format == "JPEG" else f"image/{image_format.lower()}"
        return image_format, width, height, canonical


def validate_image_bytes(content: bytes, declared_mime: str) -> tuple[str, int, int, str]:
    """Apply the same bounded MIME/magic/decode checks to provider image outputs."""
    return AssetResolver._validate_image(content, declared_mime)
