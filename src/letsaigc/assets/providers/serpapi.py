"""Google Images first-page and read-only account/archive adapters."""

import time
from dataclasses import replace

from pydantic import TypeAdapter

from ...pipelines.errors import PipelineError
from ...schemas.pipeline import Cost, Identifier
from ..search import SearchCandidate, SearchReceipt, normalize_serpapi
from .base import SearchHTTPProvider, safe_page, safe_text


class SerpApiProvider(SearchHTTPProvider):
    provider = "serpapi"
    credential_env = "SERPAPI_API_KEY"
    origin = "https://serpapi.com"

    def search(self, request):
        price = self.prepare(request, "search")
        status, payload, headers = self.http(
            "GET",
            "/search.json",
            params={
                "engine": "google_images",
                "q": self.query(request),
                "ijn": "0",
                "no_cache": "true",
                "api_key": self.credential,
            },
        )
        return self.parse(request, price, status, payload, headers)

    def parse(self, request, price, status, payload, headers):
        metadata = payload.get("search_metadata") or {}
        if status != 200 or metadata.get("status") != "Success":
            return self.failure(status, payload, headers)
        try:
            identity = TypeAdapter(Identifier).validate_python(metadata["id"])
            rows = payload.get("images_results", [])
            if not isinstance(rows, list):
                raise ValueError()
            candidates = []
            for index, row in enumerate(rows[: request.candidate_limit]):
                if not isinstance(row, dict) or not isinstance(row.get("original"), str):
                    continue
                candidates.append(
                    SearchCandidate(
                        candidate_id=f"candidate-{index + 1}",
                        provider=self.provider,
                        original_url=row["original"],
                        source_page=safe_page(row.get("link")),
                        title=safe_text(row.get("title"), self.credential),
                        declared_width=row.get("original_width") if type(row.get("original_width")) is int else None,
                        declared_height=row.get("original_height") if type(row.get("original_height")) is int else None,
                    )
                )
            # no_cache is frozen in the approved policy: successful requests
            # consume one search; elapsed time is never used as billing evidence.
            return SearchReceipt(
                provider=self.provider,
                request_id=identity,
                candidates=tuple(candidates),
                units=1,
                actual=Cost(cost_usd=price.search_unit_usd),
                status="ready" if candidates else "empty",
            )
        except (ValueError, KeyError, TypeError):
            return self.failure(0, {}, {})

    def quota(self, request):
        self.prepare(request, "probe")
        status, payload, _ = self.http("GET", "/account.json", params={"api_key": self.credential})
        if status != 200:
            raise PipelineError("quota_unavailable")
        return normalize_serpapi(payload, scope_id=request.scope_id, observed_at=int(time.time()))

    def archive(self, request, search_id):
        price = self.prepare(request, "archive")
        identity = TypeAdapter(Identifier).validate_python(search_id)
        status, payload, headers = self.http("GET", f"/searches/{identity}.json", params={"api_key": self.credential})
        receipt = self.parse(request, price, status, payload, headers)
        return replace(receipt, retrieval_actual=Cost(cost_usd=price.archive_actual_usd) if status == 200 else None)
