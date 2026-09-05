"""Basic-only Tavily image search; Usage requires an explicit price contract."""

import time

from pydantic import TypeAdapter

from ...pipelines.errors import PipelineError
from ...schemas.pipeline import Cost, Identifier
from ..search import SearchCandidate, SearchReceipt, normalize_tavily
from .base import SearchHTTPProvider, safe_page, safe_text


class TavilyProvider(SearchHTTPProvider):
    provider = "tavily"
    credential_env = "TAVILY_API_KEY"
    origin = "https://api.tavily.com"

    def search(self, request):
        price = self.prepare(request, "search")
        status, payload, headers = self.http(
            "POST",
            "/search",
            headers={"Authorization": "Bearer " + self.credential},
            json={
                "query": self.query(request),
                "search_depth": "basic",
                "auto_parameters": False,
                "max_results": 5,
                "include_images": True,
                "include_image_descriptions": True,
                "include_answer": False,
                "include_raw_content": False,
                "include_usage": True,
            },
        )
        if status != 200:
            return self.failure(status, payload, headers)
        try:
            identity = TypeAdapter(Identifier).validate_python(payload["request_id"])
            units = (payload.get("usage") or {}).get("credits")
            if type(units) is not int or units < 0:
                return self.failure(0, {}, {})
            rows = [(item, None) for item in payload.get("images", [])[: request.candidate_limit]]
            for result in payload.get("results", [])[:5]:
                rows.extend(
                    (item, safe_page(result.get("url"))) for item in result.get("images", [])[: request.candidate_limit]
                )
                if len(rows) >= request.candidate_limit:
                    break
            candidates = []
            for index, (row, source_page) in enumerate(rows[: request.candidate_limit]):
                row = {"url": row} if isinstance(row, str) else row
                if not isinstance(row, dict) or not isinstance(row.get("url"), str):
                    continue
                candidates.append(
                    SearchCandidate(
                        candidate_id=f"candidate-{index + 1}",
                        provider=self.provider,
                        original_url=row["url"],
                        source_page=source_page,
                        description=safe_text(row.get("description"), self.credential),
                    )
                )
            return SearchReceipt(
                provider=self.provider,
                request_id=identity,
                candidates=tuple(candidates),
                units=units,
                actual=Cost(cost_usd=units * price.search_unit_usd),
                status="ready" if candidates else "empty",
            )
        except (ValueError, KeyError, TypeError, AttributeError):
            return self.failure(0, {}, {})

    def quota(self, request):
        self.prepare(request, "probe")
        status, payload, _ = self.http("GET", "/usage", headers={"Authorization": "Bearer " + self.credential})
        if status != 200:
            raise PipelineError("quota_unavailable")
        return normalize_tavily(payload, scope_id=request.scope_id, observed_at=int(time.time()))
