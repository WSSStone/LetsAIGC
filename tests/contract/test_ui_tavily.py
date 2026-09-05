import json

import httpx
import pytest

from letsaigc.assets.providers.tavily import TavilyProvider
from letsaigc.assets.search import QuotaRequest
from letsaigc.pipelines.errors import PipelineError


def test_basic_search_parameters_usage_and_nested_images(ui_store, ui_search_request):
    calls = []

    def respond(request):
        calls.append(request)
        return httpx.Response(
            200,
            json={
                "request_id": "tavily-test",
                "usage": {"credits": 1},
                "images": [{"url": "https://example.com/a.png", "description": "HUD"}],
                "results": [
                    {"url": "https://example.com/page?secret=hidden", "images": [{"url": "https://example.com/b.png"}]}
                ],
            },
        )

    provider = TavilyProvider(
        ui_store, authorize=lambda request: None, credential="fixture-key", transport=httpx.MockTransport(respond)
    )
    result = provider.search(ui_search_request("tavily"))
    body = json.loads(calls[0].content)
    assert body == {
        "query": "game HUD",
        "search_depth": "basic",
        "auto_parameters": False,
        "max_results": 5,
        "include_images": True,
        "include_image_descriptions": True,
        "include_answer": False,
        "include_raw_content": False,
        "include_usage": True,
    }
    assert calls[0].headers["Authorization"] == "Bearer fixture-key"
    assert "x-project-id" not in calls[0].headers
    assert result.units == 1 and result.actual.cost_usd == 0.01
    assert len(result.candidates) == 2 and result.candidates[1].source_page == "https://example.com/page"


def test_unknown_probe_price_is_rejected_without_http(ui_store, ui_search_request):
    request = ui_search_request("tavily", probe_known=False)
    provider = TavilyProvider(
        ui_store,
        authorize=lambda request: None,
        credential="key",
        transport=httpx.MockTransport(lambda request: pytest.fail("Unpriced probe must not be called")),
    )
    with pytest.raises(PipelineError, match="pricing_unavailable"):
        provider.quota(
            QuotaRequest(
                task_id=request.task_id,
                operation_id=request.operation_id,
                provider="tavily",
                scope_id="scope-test",
                pricing_ref=request.pricing_ref,
            )
        )


@pytest.mark.parametrize(
    "status,code", [(432, "quota_exhausted"), (433, "payg_limit"), (429, "rate_limited"), (500, "outcome_unknown")]
)
def test_errors_do_not_enable_paygo_or_retry(ui_store, ui_search_request, status, code):
    calls = []

    def respond(request):
        calls.append(request)
        return httpx.Response(status, headers={"Retry-After": "1200"}, json={"detail": {"error": "private"}})

    provider = TavilyProvider(
        ui_store, authorize=lambda request: None, credential="key", transport=httpx.MockTransport(respond)
    )
    result = provider.search(ui_search_request("tavily"))
    assert result.error_code == code and len(calls) == 1
    if status == 500:
        assert result.actual is None and result.status == "unknown"
    else:
        assert result.cooldown_until >= 1200
