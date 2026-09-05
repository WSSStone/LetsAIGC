"""Reference-only search contracts and pure quota normalization/routing."""

from dataclasses import dataclass, field
from datetime import date
from typing import Literal, Protocol

from pydantic import Field, model_validator

from ..pipelines.errors import PipelineError
from ..schemas.pipeline import ArtifactRef, Cost, Digest, Identifier, PipelineModel

Provider = Literal["serpapi", "tavily"]


class SearchPricing(PipelineModel):
    schema_version: Literal[1] = 1
    provider: Provider
    search_upper_usd: float = Field(ge=0)
    search_unit_usd: float | None = Field(default=None, ge=0)
    probe_upper_usd: float | None = Field(default=None, ge=0)
    probe_actual_usd: float | None = Field(default=None, ge=0)
    search_basis: str = Field(min_length=1, max_length=2048)
    probe_basis: str | None = Field(default=None, max_length=2048)
    archive_upper_usd: float | None = Field(default=None, ge=0)
    archive_actual_usd: float | None = Field(default=None, ge=0)
    archive_basis: str | None = Field(default=None, max_length=2048)
    terms_version: str | None = Field(default=None, max_length=128)
    reviewed_until: date

    def ready(self, kind, *, today=None):
        day = date.fromisoformat(today) if isinstance(today, str) else today or date.today()
        if day > self.reviewed_until or self.terms_version is None:
            return False
        if kind == "search":
            return self.search_unit_usd is not None and self.search_unit_usd <= self.search_upper_usd
        if kind == "archive":
            return (
                self.archive_basis is not None
                and self.archive_actual_usd is not None
                and self.archive_upper_usd is not None
                and self.archive_actual_usd <= self.archive_upper_usd
            )
        return (
            self.probe_basis is not None
            and self.probe_actual_usd is not None
            and self.probe_upper_usd is not None
            and self.probe_actual_usd <= self.probe_upper_usd
        )


class RoutingPolicy(PipelineModel):
    schema_version: Literal[1] = 1
    mode: Literal["quota_aware"] = "quota_aware"
    allowed_providers: list[Provider] = Field(default_factory=lambda: ["serpapi", "tavily"], min_length=1, max_length=2)
    preferred_provider: Literal["serpapi"] = "serpapi"
    allow_payg: Literal[False] = False
    serpapi_no_cache: Literal[True] = True
    refresh_seconds: int = Field(default=75, ge=75, strict=True)
    snapshot_ttl_seconds: int = Field(default=300, ge=75, le=300, strict=True)
    max_probes_per_provider: int = Field(default=2, ge=1, le=2, strict=True)
    probes_per_ten_minutes: int = Field(default=10, ge=1, le=10, strict=True)
    low_watermark: Literal[0.1] = 0.1
    return_watermark: Literal[0.2] = 0.2
    max_attempts: int = Field(default=3, ge=1, le=3, strict=True)
    max_switches: int = Field(default=2, ge=0, le=2, strict=True)

    @model_validator(mode="after")
    def unique_providers(self):
        if len(self.allowed_providers) != len(set(self.allowed_providers)):
            raise ValueError("Duplicate provider")
        return self


class SearchCriteria(PipelineModel):
    schema_version: Literal[1] = 1
    minimum_width: int = Field(default=128, ge=1, le=8192, strict=True)
    minimum_height: int = Field(default=128, ge=1, le=8192, strict=True)
    minimum_aspect_ratio: float = Field(default=0.1, gt=0)
    maximum_aspect_ratio: float = Field(default=10, gt=0)
    perceptual_hash: Literal["dct-64-v1"] = "dct-64-v1"
    duplicate_distance: Literal[6] = 6
    minimum_contrast: float = Field(default=2, ge=0, le=128)

    @model_validator(mode="after")
    def valid_range(self):
        if self.minimum_aspect_ratio > self.maximum_aspect_ratio:
            raise ValueError("Invalid aspect ratio range")
        return self


class SearchRequest(PipelineModel):
    schema_version: Literal[1] = 1
    task_id: Identifier
    operation_id: Identifier
    logical_query_id: Identifier
    provider: Provider
    query_ref: ArtifactRef
    pricing_ref: ArtifactRef
    policy_hash: Digest
    serpapi_no_cache: Literal[True] = True
    candidate_limit: int = Field(default=20, ge=1, le=20, strict=True)
    download_limit: int = Field(default=5, ge=1, le=5, strict=True)

    @model_validator(mode="after")
    def scope(self):
        if any(ref.task_id != self.task_id for ref in (self.query_ref, self.pricing_ref)):
            raise ValueError("Cross-task search request")
        return self


class QuotaRequest(PipelineModel):
    task_id: Identifier
    operation_id: Identifier
    provider: Provider
    scope_id: Identifier
    pricing_ref: ArtifactRef


class QuotaObservation(PipelineModel):
    schema_version: Literal[1] = 1
    provider: Provider
    scope_id: Identifier
    unit: Literal["search", "credit"]
    status: Literal["known", "exhausted", "unknown", "stale"]
    remaining: int | None = Field(default=None, ge=0, strict=True)
    total_limit: int | None = Field(default=None, gt=0, strict=True)
    plan_remaining: int | None = Field(default=None, ge=0, strict=True)
    extra_remaining: int | None = Field(default=None, ge=0, strict=True)
    key_remaining: int | None = Field(default=None, ge=0, strict=True)
    paygo_usage: int | None = Field(default=None, ge=0, strict=True)
    paygo_limit: int | None = Field(default=None, ge=0, strict=True)
    observed_at: int = Field(ge=0, strict=True)
    reset_at: str | None = Field(default=None, max_length=64)
    cooldown_until: int = Field(default=0, ge=0, strict=True)

    @model_validator(mode="after")
    def consistent_balance(self):
        if self.status == "known" and (self.remaining is None or self.remaining == 0):
            raise ValueError("Known eligible balance must be positive")
        if self.status == "exhausted" and self.remaining != 0:
            raise ValueError("Exhaustion requires a known zero")
        if self.unit != ("search" if self.provider == "serpapi" else "credit"):
            raise ValueError("Incorrect provider unit")
        return self


class RouteDecision(PipelineModel):
    task_id: Identifier
    operation_id: Identifier
    logical_query_id: Identifier
    attempt_no: int = Field(ge=1, le=3, strict=True)
    provider: Provider
    scope_id: Identifier
    snapshot_id: Identifier
    policy_hash: Digest
    reason: Identifier


@dataclass(frozen=True, repr=False)
class SearchCandidate:
    """Ephemeral provider content. Never serialize this object to history or logs."""

    candidate_id: str
    provider: str
    original_url: str = field(repr=False)
    source_page: str | None = None
    title: str = field(default="", repr=False)
    description: str = field(default="", repr=False)
    declared_width: int | None = None
    declared_height: int | None = None


@dataclass(frozen=True, repr=False)
class SearchReceipt:
    provider: str
    request_id: str | None
    candidates: tuple[SearchCandidate, ...]
    units: int | None
    actual: Cost | None
    status: str
    error_code: str | None = None
    cooldown_until: int = 0
    retrieval_actual: Cost | None = None


class ImageSearchProvider(Protocol):
    def search(self, request: SearchRequest) -> SearchReceipt: ...
    def quota(self, request: QuotaRequest) -> QuotaObservation: ...


def _count(value):
    return value if type(value) is int and value >= 0 else None


def normalize_serpapi(payload, *, scope_id, observed_at):
    values = {
        name: _count(payload.get(name))
        for name in ("plan_searches_left", "extra_credits", "total_searches_left", "searches_per_month")
    }
    plan, extra, total, limit = values.values()
    # Inconsistent observations cannot create artificial spendable credit.
    remaining = min(total, plan + extra) if all(value is not None for value in (plan, extra, total)) else None
    return QuotaObservation(
        provider="serpapi",
        scope_id=scope_id,
        unit="search",
        observed_at=observed_at,
        status="unknown" if remaining is None else "known" if remaining else "exhausted",
        remaining=remaining,
        total_limit=limit or None,
        plan_remaining=plan,
        extra_remaining=extra,
    )


def normalize_tavily(payload, *, scope_id, observed_at):
    account, key = payload.get("account") or {}, payload.get("key") or {}
    usage, limit = _count(account.get("plan_usage")), _count(account.get("plan_limit"))
    key_usage, key_limit = _count(key.get("usage")), _count(key.get("limit"))
    plan = max(0, limit - usage) if usage is not None and limit is not None else None
    # Zero/null key limits have no verified unlimited meaning in this contract.
    key_remaining = max(0, key_limit - key_usage) if key_usage is not None and key_limit else None
    remaining = min(plan, key_remaining) if plan is not None and key_remaining is not None else 0 if plan == 0 else None
    return QuotaObservation(
        provider="tavily",
        scope_id=scope_id,
        unit="credit",
        observed_at=observed_at,
        status="unknown" if remaining is None else "known" if remaining else "exhausted",
        remaining=remaining,
        total_limit=min(limit, key_limit) if limit and key_limit else limit or None,
        plan_remaining=plan,
        key_remaining=key_remaining,
        paygo_usage=_count(account.get("paygo_usage")),
        paygo_limit=_count(account.get("paygo_limit")),
    )


def choose_route(policy, snapshots, *, now, current=None, excluded=frozenset()):
    eligible = {
        provider: item
        for provider, item in snapshots.items()
        if provider in policy.allowed_providers
        and provider not in excluded
        and item.status == "known"
        and item.remaining
        and 0 <= now - item.observed_at < policy.snapshot_ttl_seconds
        and now >= item.cooldown_until
    }
    if not eligible:
        raise PipelineError("search_unavailable", "search_unavailable")
    if len(eligible) == 1:
        return next(iter(eligible)), "only_eligible"
    preferred = policy.preferred_provider
    other = "tavily"
    first, second = eligible[preferred], eligible[other]
    ratio = first.remaining / first.total_limit if first.total_limit else None
    other_ratio = second.remaining / second.total_limit if second.total_limit else None
    if current == other:
        if ratio is not None and ratio >= policy.return_watermark:
            return preferred, "preferred_recovered"
        return other, "retained_provider"
    if ratio is not None and other_ratio is not None and ratio < policy.low_watermark and other_ratio > ratio:
        return other, "preferred_low_watermark"
    return preferred, "preferred_provider"
