import logging

import httpx
import pytest

from letsaigc.assets.providers.serpapi import SerpApiProvider
from letsaigc.assets.search import QuotaRequest, SearchPricing
from letsaigc.pipelines.errors import PipelineError
from letsaigc.schemas.pipeline import canonical_json


def test_first_page_parameters_and_ephemeral_urls(ui_store, ui_search_request, caplog):
    calls, authorized = [], []
    secret = "synthetic-serpapi-key"

    def respond(request):
        calls.append(request)
        return httpx.Response(
            200,
            json={
                "search_metadata": {"id": "search-id", "status": "Success"},
                "images_results": [
                    {
                        "original": "https://example.com/image.png?signature=transient",
                        "title": "HUD",
                        "link": "https://example.com/page?tracking=private",
                        "original_width": 600,
                        "original_height": 900,
                    }
                ]
                * 25,
            },
        )

    provider = SerpApiProvider(
        ui_store,
        authorize=lambda request: authorized.append(request.operation_id),
        credential=secret,
        transport=httpx.MockTransport(respond),
    )
    with caplog.at_level(logging.DEBUG):
        result = provider.search(ui_search_request("serpapi"))
    assert authorized == ["op-test"] and len(calls) == 1
    assert dict(calls[0].url.params) == {
        "engine": "google_images",
        "q": "game HUD",
        "ijn": "0",
        "no_cache": "true",
        "api_key": secret,
    }
    assert len(result.candidates) == 20 and result.units == 1
    assert result.candidates[0].source_page == "https://example.com/page"
    assert "transient" in result.candidates[0].original_url
    assert secret not in caplog.text and "transient" not in repr(result)


def test_account_whitelist_and_unknown_failure_is_not_zero(ui_store, ui_search_request):
    request = ui_search_request("serpapi")
    calls = []

    def respond(http_request):
        calls.append(http_request)
        if http_request.url.path == "/account.json":
            return httpx.Response(
                200,
                json={
                    "api_key": "secret",
                    "account_email": "private@example.com",
                    "plan_searches_left": 3,
                    "extra_credits": 0,
                    "total_searches_left": 3,
                    "searches_per_month": 100,
                },
            )
        return httpx.Response(503, json={"error": "secret"})

    provider = SerpApiProvider(
        ui_store, authorize=lambda request: None, credential="test-key", transport=httpx.MockTransport(respond)
    )
    quota = provider.quota(
        QuotaRequest(
            task_id=request.task_id,
            operation_id=request.operation_id,
            provider="serpapi",
            scope_id="scope-test",
            pricing_ref=request.pricing_ref,
        )
    )
    assert quota.remaining == 3 and "private" not in quota.model_dump_json()
    failed = provider.search(request)
    assert failed.status == "unknown" and failed.actual is None and failed.units is None
    assert len(calls) == 2


def test_authorization_guard_runs_before_any_http(ui_store, ui_search_request):
    def reject(request):
        raise PipelineError("approval_required")

    def unexpected(request):
        pytest.fail("An unapproved request reached HTTP")

    provider = SerpApiProvider(ui_store, authorize=reject, credential="test", transport=httpx.MockTransport(unexpected))
    with pytest.raises(PipelineError):
        provider.search(ui_search_request("serpapi"))


def test_archive_requires_its_own_known_price_and_never_reposts_search(ui_store, ui_search_request):
    calls = []

    def respond(request):
        calls.append(request)
        return httpx.Response(200, json={"search_metadata": {"id": "original-search", "status": "Success"}})

    provider = SerpApiProvider(
        ui_store, authorize=lambda request: None, credential="synthetic", transport=httpx.MockTransport(respond)
    )
    request = ui_search_request("serpapi")
    with pytest.raises(PipelineError, match="pricing_unavailable"):
        provider.archive(request, "original-search")
    assert calls == []
    price = SearchPricing.model_validate_json(ui_store.read(request.pricing_ref)).model_copy(
        update={"archive_upper_usd": 0.001, "archive_actual_usd": 0.001, "archive_basis": "fixture-account-terms"}
    )
    ref = ui_store.put(request.task_id, "plan", canonical_json(price).encode(), role="pricing")
    result = provider.archive(request.model_copy(update={"pricing_ref": ref}), "original-search")
    assert len(calls) == 1 and calls[0].method == "GET"
    assert calls[0].url.path == "/searches/original-search.json"
    assert result.request_id == "original-search" and result.retrieval_actual.cost_usd == 0.001
