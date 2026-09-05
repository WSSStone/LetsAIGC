"""Shared UI test isolation; live tests require a separate, explicit opt-in."""

from collections import Counter
from pathlib import Path

import pytest


def pytest_addoption(parser):
    parser.addoption("--ui-live", action="store_true", default=False, help="Enable approved UI live acceptance")


def pytest_collection_modifyitems(config, items):
    if not config.getoption("--ui-live"):
        disabled = pytest.mark.skip(reason="UI live acceptance requires --ui-live and task-specific approval")
        for item in items:
            if item.get_closest_marker("ui_live"):
                item.add_marker(disabled)


@pytest.fixture
def ui_fixture_dir():
    return Path(__file__).parent / "fixtures" / "ui_analysis"


@pytest.fixture
def ui_workspace(tmp_path):
    root = tmp_path / "ui"
    root.mkdir()
    return root


@pytest.fixture
def ui_store(ui_workspace):
    from letsaigc.assets.store import ArtifactStore

    return ArtifactStore(ui_workspace / "artifacts")


@pytest.fixture
def ui_ledger(ui_workspace):
    from letsaigc.pipelines.ledger import Ledger

    return Ledger(ui_workspace / "ledger.sqlite")


@pytest.fixture
def ui_calls():
    return Counter()


@pytest.fixture
def ui_search_request(ui_store):
    from letsaigc.assets.search import SearchPricing, SearchRequest
    from letsaigc.schemas.pipeline import canonical_json

    def build(provider, *, probe_known=True):
        price = SearchPricing(
            provider=provider,
            search_upper_usd=0.025,
            search_unit_usd=0.01,
            probe_upper_usd=0 if probe_known else None,
            probe_actual_usd=0 if probe_known else None,
            probe_basis="https://example.com/probe" if probe_known else None,
            search_basis="https://example.com/search",
            terms_version="fixture",
            reviewed_until="2099-01-01",
        )
        query = ui_store.put("search-test", "plan", b'{"queries":["game HUD"]}', role="query")
        pricing = ui_store.put("search-test", "plan", canonical_json(price).encode(), role="pricing")
        return SearchRequest(
            task_id="search-test",
            operation_id="op-test",
            provider=provider,
            logical_query_id="query-1",
            query_ref=query,
            pricing_ref=pricing,
            policy_hash="a" * 64,
        )

    return build
