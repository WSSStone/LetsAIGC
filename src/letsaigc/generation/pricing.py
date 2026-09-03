from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from ..config import load_yaml
from ..errors import PolicyError
from ..paths import find_repo_root


class PricingEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    effective_at: date
    review_after: date
    source: str
    agent_model: str = "gpt-5.6-luna"
    rates: dict[str, float]
    common_output_cost_usd: dict[str, dict[str, float]]


def load_pricing(path: Path | None = None) -> PricingEntry:
    source = path or find_repo_root() / "configs/providers/openai-pricing.yaml"
    return PricingEntry.model_validate(load_yaml(source))


def ensure_current(pricing: PricingEntry, *, today: date | None = None) -> None:
    current = today or date.today()
    if current < pricing.effective_at or current > pricing.review_after:
        raise PolicyError(
            "Provider pricing is past its review date",
            details={"pricing_id": pricing.id, "review_after": pricing.review_after.isoformat()},
        )


def image_output_reservation(pricing: PricingEntry, *, size: str, quality: str) -> float:
    ensure_current(pricing)
    try:
        return pricing.common_output_cost_usd[size][quality]
    except KeyError as exc:
        raise PolicyError(f"No current price for image output {size}/{quality}") from exc


def calculate_actual_cost(pricing: PricingEntry, usage: dict[str, Any]) -> float:
    ensure_current(pricing)
    total = 0.0
    for usage_key, rate_key in (
        ("text_input_tokens", "text_input_per_million"),
        ("image_input_tokens", "image_input_per_million"),
        ("image_output_tokens", "image_output_per_million"),
    ):
        total += float(usage.get(usage_key, 0)) * pricing.rates[rate_key] / 1_000_000
    return round(total, 8)


def calculate_luna_cost(
    pricing: PricingEntry, usage: dict[str, Any], *, model: str = "gpt-5.6-luna"
) -> float:
    ensure_current(pricing)
    if model != pricing.agent_model:
        raise PolicyError(f"No reviewed Agent token price for model: {model}")
    input_tokens = float(usage.get("input_tokens", 0))
    cached_tokens = float(usage.get("cached_input_tokens", 0))
    output_tokens = float(usage.get("output_tokens", 0))
    uncached = max(0, input_tokens - cached_tokens)
    total = (
        uncached * pricing.rates["luna_input_per_million"]
        + cached_tokens * pricing.rates["luna_cached_input_per_million"]
        + output_tokens * pricing.rates["luna_output_per_million"]
    ) / 1_000_000
    return round(total, 8)
