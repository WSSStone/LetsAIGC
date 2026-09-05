"""Bounded HTTP and credential-safe diagnostics for ephemeral provider content."""

import json
import re
import time
from datetime import UTC
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit, urlunsplit

import httpx

from ...config import get_setting
from ...pipelines.errors import PipelineError
from ...schemas.pipeline import Cost
from ..http_safety import private_http
from ..search import SearchPricing, SearchReceipt


def safe_page(value):
    try:
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
            return None
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))[:2048]
    except (ValueError, TypeError):
        return None


def safe_text(value, secret):
    text = value[:4096] if isinstance(value, str) else ""
    text = text.replace(secret, "[REDACTED]") if secret else text
    return re.sub(r"https?://[^\s<>\"]+", lambda match: safe_page(match[0]) or "[URL]", text)


def retry_at(headers, *, now):
    value = headers.get("retry-after")
    try:
        return now + max(0, int(value))
    except (ValueError, TypeError):
        try:
            parsed = parsedate_to_datetime(value)
            return max(now, int(parsed.replace(tzinfo=parsed.tzinfo or UTC).timestamp()))
        except (ValueError, TypeError, OverflowError):
            return now + 60


class SearchHTTPProvider:
    provider = ""
    credential_env = ""
    origin = ""

    def __init__(self, store, *, authorize, transport=None, credential=None):
        self.store, self.authorize, self.transport = store, authorize, transport
        self.credential = get_setting(self.credential_env, "") if credential is None else credential

    def prepare(self, request, kind):
        # The caller binds this guard to the already admitted common operation.
        self.authorize(request)
        if request.provider != self.provider or request.pricing_ref.task_id != request.task_id:
            raise PipelineError("operation_scope")
        price = SearchPricing.model_validate_json(self.store.read(request.pricing_ref))
        if price.provider != self.provider or not price.ready(kind):
            raise PipelineError("pricing_unavailable", "pricing_unavailable")
        if not self.credential:
            raise PipelineError("missing_credentials")
        return price

    def query(self, request):
        payload = json.loads(self.store.read(request.query_ref))
        queries = payload.get("queries")
        if (
            request.query_ref.task_id != request.task_id
            or not isinstance(queries, list)
            or not 1 <= len(queries) <= 3
            or any(not isinstance(item, str) or not 1 <= len(item.strip()) <= 1024 for item in queries)
        ):
            raise PipelineError("invalid_query")
        try:
            index = int(request.logical_query_id.removeprefix("query-")) - 1
            if not 0 <= index < len(queries):
                raise ValueError()
            return queries[index]
        except (ValueError, IndexError):
            raise PipelineError("invalid_query") from None

    def http(self, method, path, **kwargs):
        try:
            with (
                private_http(),
                httpx.Client(
                    base_url=self.origin, timeout=30, follow_redirects=False, trust_env=False, transport=self.transport
                ) as client,
            ):
                with client.stream(method, path, **kwargs) as response:
                    body = bytearray()
                    for chunk in response.iter_bytes():
                        body.extend(chunk)
                        if len(body) > 2 * 1024**2:
                            raise ValueError("Response size limit")
                    payload = json.loads(body)
                    if not isinstance(payload, dict):
                        raise ValueError("Invalid response")
                    return response.status_code, payload, {"retry-after": response.headers.get("retry-after")}
        except Exception:
            # Never chain a request exception containing authentication or signed URLs.
            return 0, {}, {}

    def failure(self, status, payload, headers):
        code = {
            401: "auth_failed",
            403: "auth_failed",
            429: "rate_limited",
            432: "quota_exhausted",
            433: "payg_limit",
        }.get(status)
        if (
            self.provider == "serpapi"
            and status == 429
            and "run out of searches" in str(payload.get("error", "")).lower()
        ):
            code = "quota_exhausted"
        # These documented admission rejections prove no search was accepted.
        # Unknown 4xx/5xx, timeouts and malformed responses retain the reservation.
        rejected = code is not None
        return SearchReceipt(
            provider=self.provider,
            request_id=None,
            candidates=(),
            units=0 if rejected else None,
            actual=Cost() if rejected else None,
            status="rejected" if rejected else "unknown",
            error_code=code or "outcome_unknown",
            cooldown_until=retry_at(headers, now=int(time.time())),
        )
