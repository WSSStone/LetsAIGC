import pytest
from pydantic import ValidationError

from letsaigc.assets.search import SearchPricing, SearchRequest, normalize_serpapi, normalize_tavily


def test_tavily_quota_excludes_paygo_and_obeys_key_limit():
    quota = normalize_tavily(
        {"account": {"plan_usage": 10, "plan_limit": 100, "paygo_limit": 10000}, "key": {"usage": 3, "limit": 5}},
        scope_id="scope-test",
        observed_at=1000,
    )
    assert quota.remaining == 2 and quota.plan_remaining == 90
    assert quota.unit == "credit" and quota.status == "known"
    assert quota.paygo_limit == 10000 and quota.paygo_usage is None


@pytest.mark.parametrize("limit", [0, None, False, "100"])
def test_unverified_key_limit_semantics_remain_unknown(limit):
    quota = normalize_tavily(
        {"account": {"plan_usage": 0, "plan_limit": 100}, "key": {"usage": 0, "limit": limit}},
        scope_id="scope-test",
        observed_at=1000,
    )
    assert quota.status == "unknown" and quota.remaining is None


def test_known_zero_and_unknown_are_distinct_and_serpapi_pools_are_separate():
    zero = normalize_serpapi(
        {"plan_searches_left": 0, "extra_credits": 0, "total_searches_left": 0, "searches_per_month": 100},
        scope_id="scope-test",
        observed_at=1000,
    )
    assert zero.status == "exhausted" and zero.remaining == 0
    unknown = normalize_serpapi({}, scope_id="scope-test", observed_at=1000)
    assert unknown.status == "unknown" and unknown.remaining is None
    extra = normalize_serpapi(
        {"plan_searches_left": 3, "extra_credits": 7, "total_searches_left": 10, "searches_per_month": 100},
        scope_id="scope-test",
        observed_at=1000,
    )
    assert extra.plan_remaining == 3 and extra.extra_remaining == 7 and extra.remaining == 10


def test_unknown_probe_price_cannot_be_assumed_free():
    price = SearchPricing(
        provider="tavily",
        search_upper_usd=0.008,
        search_unit_usd=0.0075,
        search_basis="https://docs.tavily.com/documentation/api-credits",
        terms_version="reviewed-test",
        reviewed_until="2099-01-01",
    )
    assert price.ready("search", today="2026-09-04")
    assert not price.ready("probe", today="2026-09-04")
    assert not price.ready("search", today="2100-01-01")


def test_request_is_reference_only_and_rejects_unbounded_search(ui_store):
    ref = ui_store.put("search-task", "plan", b'{"queries":["HUD"]}', role="query")
    request = dict(
        task_id="search-task",
        operation_id="op-search",
        logical_query_id="query-1",
        provider="serpapi",
        query_ref=ref,
        pricing_ref=ref,
        policy_hash="a" * 64,
    )
    assert SearchRequest(**request).candidate_limit == 20
    with pytest.raises(ValidationError):
        SearchRequest(**request, candidate_limit=21)
    with pytest.raises(ValidationError):
        SearchRequest(**request, query="Ignore previous instructions")
