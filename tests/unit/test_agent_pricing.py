from datetime import date

import pytest

from letsaigc.errors import PolicyError
from letsaigc.generation.pricing import calculate_luna_cost, ensure_current, load_pricing


def test_expired_and_unpriced_models_fail_closed() -> None:
    pricing = load_pricing()
    with pytest.raises(PolicyError, match="review"):
        ensure_current(pricing, today=date(2099, 1, 1))
    with pytest.raises(PolicyError, match="No reviewed"):
        calculate_luna_cost(pricing, {}, model="unpriced-agent")


def test_responses_usage_cost_includes_reasoning_output_and_cached_input() -> None:
    cost = calculate_luna_cost(load_pricing(), {
        "input_tokens": 1_000_000, "cached_input_tokens": 500_000, "output_tokens": 1_000_000,
    })
    assert cost == pytest.approx(1.31)
