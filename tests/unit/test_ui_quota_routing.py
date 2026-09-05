import pytest

from letsaigc.assets.search import QuotaObservation, RoutingPolicy, choose_route
from letsaigc.pipelines.errors import PipelineError


def quota(provider, remaining, limit=100):
    return QuotaObservation(
        provider=provider,
        scope_id="scope-" + provider,
        unit="search" if provider == "serpapi" else "credit",
        status="known" if remaining else "exhausted",
        remaining=remaining,
        total_limit=limit,
        observed_at=1000,
    )


def test_preference_low_watermark_and_hysteresis():
    policy = RoutingPolicy()
    assert (
        choose_route(policy, {"serpapi": quota("serpapi", 50), "tavily": quota("tavily", 80)}, now=1100)[0] == "serpapi"
    )
    assert (
        choose_route(policy, {"serpapi": quota("serpapi", 9), "tavily": quota("tavily", 80)}, now=1100)[0] == "tavily"
    )
    assert (
        choose_route(
            policy, {"serpapi": quota("serpapi", 15), "tavily": quota("tavily", 80)}, now=1100, current="tavily"
        )[0]
        == "tavily"
    )
    assert (
        choose_route(
            policy, {"serpapi": quota("serpapi", 20), "tavily": quota("tavily", 80)}, now=1100, current="tavily"
        )[0]
        == "serpapi"
    )


def test_one_eligible_none_eligible_and_unknown_ratios():
    policy = RoutingPolicy()
    assert choose_route(policy, {"serpapi": quota("serpapi", 0), "tavily": quota("tavily", 5)}, now=1100)[0] == "tavily"
    with pytest.raises(PipelineError, match="search_unavailable"):
        choose_route(policy, {"serpapi": quota("serpapi", 0), "tavily": quota("tavily", 0)}, now=1100)
    assert (
        choose_route(policy, {"serpapi": quota("serpapi", 1, None), "tavily": quota("tavily", 50)}, now=1100)[0]
        == "serpapi"
    )
    with pytest.raises(PipelineError):
        choose_route(policy, {"serpapi": quota("serpapi", 100)}, now=1400)
    assert (
        choose_route(
            policy, {"serpapi": quota("serpapi", 50), "tavily": quota("tavily", 50)}, now=1100, excluded={"serpapi"}
        )[0]
        == "tavily"
    )
