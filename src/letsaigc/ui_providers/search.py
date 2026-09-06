"""Bounded search acquisition; all effects use the existing task ledger."""

import hashlib
import json
import math
import re
import time
from datetime import UTC, datetime
from io import BytesIO

from PIL import Image, ImageStat

from ..assets.providers.base import private_http
from ..assets.providers.serpapi import SerpApiProvider
from ..assets.providers.tavily import TavilyProvider
from ..assets.resolver import AssetResolver
from ..assets.search import QuotaRequest, RoutingPolicy, SearchCriteria, SearchPricing, SearchRequest
from ..assets.search_routing import QuotaLedger
from ..pipelines.errors import OutcomeUnknown, PipelineError
from ..schemas.pipeline import ArtifactRef, Cost, canonical_json, digest
from ..schemas.ui import UIAnalysisRequest, UIStepBinding
from ..schemas.ui_provider import UIProvisionResult, UISource
from ..ui_analysis.normalize import validate_ui_image


def metadata_rejection(title, description, terms):
    """Exclude explicit ranking graphics; remaining term matches are not visual verification."""
    declared = (title + " " + description).casefold()
    if re.search(
        r"\btier[\s_-]*lists?\b|\bcharacter(?:s)?\s+(?:rankings?|ratings?)\b"
        r"|角色评级|角色排行|角色梯度|强度榜|强度排行|节奏榜",
        declared,
    ):
        return "non_ui_tier_list"
    if not any(term in declared for term in terms if len(term) >= 2):
        return "query_mismatch"
    return None


def perceptual_hash(image):
    """Version dct-64-v1: 32x32 luma, 8x8 DCT median, DC bit excluded."""
    reduced = image.convert("L").resize((32, 32), Image.Resampling.LANCZOS)
    pixels = [reduced.getpixel((x, y)) for y in range(32) for x in range(32)]
    cosines = [[math.cos(math.pi * (2 * index + 1) * freq / 64) for index in range(32)] for freq in range(8)]
    rows = [[sum(pixels[y * 32 + x] * cosines[u][x] for x in range(32)) for y in range(32)] for u in range(8)]
    values = [sum(rows[u][y] * cosines[v][y] for y in range(32)) for v in range(8) for u in range(8)]
    median = sorted(values[1:])[31]
    bits = sum((1 << index) for index, value in enumerate(values) if index and value > median)
    return f"{bits:016x}"


class SearchAcquisition:
    def __init__(self, service, *, transports=None, credentials=None, resolver=None):
        self.service, self.store, self.ledger = service, service.artifacts, service.ledger
        self.quotas = QuotaLedger(self.ledger)
        self.transports, self.credentials = transports or {}, credentials or {}
        self.resolver = resolver or AssetResolver()
        self.providers = {
            name: cls(
                self.store,
                authorize=self.authorize,
                transport=self.transports.get(name),
                credential=self.credentials.get(name),
            )
            for name, cls in (("serpapi", SerpApiProvider), ("tavily", TavilyProvider))
        }

    def settings(self, plan):
        request = UIAnalysisRequest.model_validate_json(
            self.store.read(ArtifactRef.model_validate(plan.parameters["request_ref"]))
        )
        if request.input.kind != "search":
            raise PipelineError("invalid_input")
        payload = json.loads(self.store.read(request.input.routing_policy_ref))
        if set(payload) != {"routing", "prices"}:
            raise PipelineError("invalid_policy")
        policy = RoutingPolicy.model_validate(payload["routing"])
        refs = {provider: ArtifactRef.model_validate(value) for provider, value in payload["prices"].items()}
        if set(refs) != set(policy.allowed_providers) or any(ref.task_id != plan.task_id for ref in refs.values()):
            raise PipelineError("artifact_scope")
        prices = {provider: SearchPricing.model_validate_json(self.store.read(ref)) for provider, ref in refs.items()}
        criteria = SearchCriteria.model_validate_json(self.store.read(request.input.criteria_ref))
        return request, policy, refs, prices, criteria

    def authorize(self, request):
        operation = self.ledger.get(request.operation_id)
        binding = self.ledger.ui_binding(request.operation_id)
        plan = self.ledger.plan(request.task_id)
        self.service.checked_plan(plan.task_id, plan.fingerprint)
        root, policy, refs, _, _ = self.settings(plan)
        if operation.task_id != request.task_id or operation.state != "submitting" or binding.capability != "ui.search":
            raise PipelineError("approval_required")
        if request.pricing_ref not in binding.inputs or refs.get(request.provider) != request.pricing_ref:
            raise PipelineError("artifact_scope")
        if isinstance(request, SearchRequest):
            route = self.quotas.route(operation.operation_id)
            if (request.provider, request.policy_hash, request.logical_query_id) != (
                route.provider,
                route.policy_hash,
                route.logical_query_id,
            ):
                raise PipelineError("route_conflict")
            if (
                request.query_ref != root.input.query_ref
                or request.query_ref not in binding.inputs
                or request.policy_hash != digest(policy)
            ):
                raise PipelineError("input_changed")
            if (
                request.candidate_limit > root.limits.candidates_per_query
                or request.download_limit > root.limits.downloads_per_query
            ):
                raise PipelineError("call_limit")
        with self.ledger.transaction() as db:
            if isinstance(request, QuotaRequest):
                probe = db.execute(
                    "SELECT scope_id,provider,root_task_id FROM quota_probes WHERE operation_id=?",
                    (request.operation_id,),
                ).fetchone()
                if probe is None or (probe["scope_id"], probe["provider"], probe["root_task_id"]) != (
                    request.scope_id,
                    request.provider,
                    request.task_id,
                ):
                    raise PipelineError("operation_scope")
            task = db.execute("SELECT approved,cancelled FROM tasks WHERE task_id=?", (plan.task_id,)).fetchone()
            if not task["approved"] or task["cancelled"]:
                raise PipelineError("approval_required")

    def refresh(self, plan, request, policy, refs, prices, scopes):
        for name, scope in scopes.items():
            now = int(time.time())
            if name in self.quotas.available({name: scope}, now=now):
                continue
            if not prices[name].ready("probe"):
                continue
            with self.ledger.transaction() as db:
                count = db.execute(
                    "SELECT COUNT(*) FROM quota_probes WHERE root_task_id=? AND provider=?", (plan.task_id, name)
                ).fetchone()[0]
            binding = UIStepBinding(
                task_id=plan.task_id,
                step_id=f"search.probe.{name}.{count + 1}",
                capability="ui.search",
                inputs=[request.input.routing_policy_ref, refs[name]],
            )
            try:
                operation = self.quotas.reserve_probe(
                    plan, request, binding, provider=name, scope_id=scope, price=prices[name], policy=policy, now=now
                )
            except PipelineError as exc:
                if exc.code in {"probe_limit", "probe_rate_limited", "probe_in_progress", "pricing_unavailable"}:
                    continue
                raise
            if not self.ledger.begin_submit(operation.operation_id):
                raise OutcomeUnknown()
            try:
                observation = self.providers[name].quota(
                    QuotaRequest(
                        task_id=plan.task_id,
                        operation_id=operation.operation_id,
                        provider=name,
                        scope_id=scope,
                        pricing_ref=refs[name],
                    )
                )
                snapshot = self.quotas.snapshot(observation)
                self.ledger.finish_ui(
                    operation.operation_id, Cost(cost_usd=prices[name].probe_actual_usd), {"snapshot_id": snapshot}
                )
            except Exception:
                # A verified free endpoint cannot accrue monetary cost even when
                # unreachable; a priced but unconfirmed probe retains its reservation.
                if prices[name].probe_actual_usd == prices[name].probe_upper_usd == 0:
                    self.ledger.finish_ui(
                        operation.operation_id, Cost(), {"error_code": "quota_unavailable"}, failed=True
                    )
                else:
                    self.ledger.uncertain(operation.operation_id)
                    raise OutcomeUnknown() from None

    def existing_sources(self, plan):
        sources = []
        for operation in self.ledger.list_operations(plan.task_id):
            if not operation.step_id.startswith("search.download.") or operation.state != "succeeded":
                continue
            refs = {ref["role"]: ArtifactRef.model_validate(ref) for ref in operation.result.get("artifacts", [])}
            if "original" not in refs or "provenance" not in refs:
                continue
            for ref in refs.values():
                self.store.read(ref)
            sources.append(
                UISource(
                    source_id="source-" + refs["original"].sha256[:24],
                    original_ref=refs["original"],
                    provenance_ref=refs["provenance"],
                    input_entry_ids=[operation.step_id],
                )
            )
        return sources

    def result(self, plan):
        sources = self.existing_sources(plan)
        rows = [
            {"source_id": source.source_id, "status": "ready", "entry_ids": source.input_entry_ids}
            for source in sources
        ]
        operations = self.ledger.list_operations(plan.task_id)
        candidates = [op.result["candidate_index_ref"] for op in operations if op.result.get("candidate_index_ref")]
        failures = [
            {"operation_id": op.operation_id, "error_code": op.result.get("error_code", "provider_failed")}
            for op in operations
            if op.state == "failed"
        ]
        summary = {
            "sources": rows,
            "candidate_indexes": candidates,
            "failures": failures,
            "counters": {
                "query_plans": 0,
                "local_relevance_checks": sum(op.result.get("candidate_count", 0) for op in operations),
                "search_attempts": sum(op.step_id.startswith("search.attempt.") for op in operations),
                "downloads": sum(op.step_id.startswith("search.download.") for op in operations),
                "probes": sum(op.step_id.startswith("search.probe.") for op in operations),
            },
        }
        index = self.store.put(plan.task_id, "acquisition", canonical_json(summary).encode(), role="input_manifest")
        return UIProvisionResult(
            provider_id="search",
            provider_version="1",
            status="ready" if sources else "empty",
            sources=sources,
            index_ref=index,
        )

    def acquire(self, plan):
        request, policy, price_refs, prices, criteria = self.settings(plan)
        if request.input.max_images != 1:
            raise PipelineError("capability_not_ready")
        if self.existing_sources(plan):
            return
        operations = self.ledger.list_operations(plan.task_id)
        if any(op.state not in {"succeeded", "failed"} for op in operations):
            raise OutcomeUnknown()
        # Lost ephemeral URLs are not permission for another paid search.
        if any(op.result.get("candidate_count", 0) for op in operations):
            raise PipelineError("input_resupply_required")
        scopes = {
            name: self.quotas.scope(name, "primary", self.providers[name].credential)
            for name in policy.allowed_providers
            if self.providers[name].credential and prices[name].ready("search")
        }
        self.refresh(plan, request, policy, price_refs, prices, scopes)
        queries = json.loads(self.store.read(request.input.query_ref)).get("queries", [])
        if not isinstance(queries, list) or not 1 <= len(queries) <= request.limits.queries:
            raise PipelineError("invalid_query")
        with self.ledger.transaction() as db:
            routes = db.execute(
                "SELECT * FROM quota_routes WHERE root_task_id=? ORDER BY rowid", (plan.task_id,)
            ).fetchall()
        attempts = len(routes)
        excluded = {row["provider"] for row in routes}
        for query_index, _ in enumerate(queries):
            while attempts < min(policy.max_attempts, request.limits.search_attempts):
                logical_query = f"query-{query_index + 1}"
                with self.ledger.transaction() as db:
                    used = db.execute(
                        """SELECT o.result FROM quota_routes r JOIN operations o ON r.operation_id=o.operation_id
                        WHERE r.root_task_id=? AND r.logical_query_id=?""",
                        (plan.task_id, logical_query),
                    ).fetchall()
                remaining_candidates = request.limits.candidates_per_query - sum(
                    json.loads(row[0]).get("candidate_count", 0) for row in used
                )
                if remaining_candidates <= 0:
                    break
                binding = UIStepBinding(
                    task_id=plan.task_id,
                    step_id=f"search.attempt.{attempts + 1}",
                    capability="ui.search",
                    inputs=[
                        request.input.query_ref,
                        request.input.criteria_ref,
                        request.input.routing_policy_ref,
                        *price_refs.values(),
                    ],
                )
                try:
                    operation, route = self.quotas.reserve_search(
                        plan,
                        request,
                        binding,
                        logical_query_id=f"query-{query_index + 1}",
                        policy=policy,
                        scopes={name: scope for name, scope in scopes.items() if name not in excluded},
                        prices=prices,
                        now=int(time.time()),
                    )
                except PipelineError as exc:
                    if exc.code == "search_unavailable":
                        break
                    raise
                attempts += 1
                excluded.add(route.provider)
                if not self.ledger.begin_submit(operation.operation_id):
                    raise OutcomeUnknown()
                try:
                    receipt = self.providers[route.provider].search(
                        SearchRequest(
                            task_id=plan.task_id,
                            operation_id=operation.operation_id,
                            logical_query_id=route.logical_query_id,
                            provider=route.provider,
                            query_ref=request.input.query_ref,
                            pricing_ref=price_refs[route.provider],
                            policy_hash=digest(policy),
                            candidate_limit=remaining_candidates,
                            download_limit=request.limits.downloads_per_query,
                        )
                    )
                except Exception:
                    self.ledger.uncertain(operation.operation_id)
                    raise OutcomeUnknown() from None
                if receipt.actual is None or receipt.status == "unknown":
                    self.ledger.uncertain(operation.operation_id)
                    raise OutcomeUnknown()
                terms = re.findall(r"[\w]+", queries[query_index].casefold())
                candidates = [
                    {
                        "candidate_id": item.candidate_id,
                        "source_page": item.source_page,
                        "title": item.title,
                        "description": item.description,
                        "declared_width": item.declared_width,
                        "declared_height": item.declared_height,
                        "metadata_relevant": metadata_rejection(item.title, item.description, terms) is None,
                        "metadata_rejection_reason": metadata_rejection(item.title, item.description, terms),
                    }
                    for item in receipt.candidates
                ]
                index = self.store.put(
                    plan.task_id, operation.operation_id, canonical_json(candidates).encode(), role="candidate_index"
                )
                result = {
                    "quota_units": receipt.units,
                    "candidate_count": len(candidates),
                    "candidate_index_ref": index.model_dump(mode="json"),
                    "provider": receipt.provider,
                    "provider_request_id": receipt.request_id,
                    "error_code": receipt.error_code,
                    "cooldown_until": receipt.cooldown_until,
                    "settled_at": int(time.time()),
                }
                settled = self.ledger.finish_ui(
                    operation.operation_id, receipt.actual, result, failed=receipt.status == "rejected"
                )
                if settled.result.get("usage_verdict") == "budget_exceeded":
                    raise PipelineError("budget_exceeded")
                if self.download(plan, request, criteria, route, receipt, index):
                    return
            excluded = set()
        raise PipelineError("search_unavailable")

    def download(self, plan, request, criteria, route, receipt, index):
        seen_sha, seen_phash = set(), []
        with self.ledger.transaction() as db:
            routes = db.execute(
                "SELECT attempt_no FROM quota_routes WHERE root_task_id=? AND logical_query_id=?",
                (plan.task_id, route.logical_query_id),
            ).fetchall()
        prefixes = tuple(f"search.download.{row[0]}." for row in routes)
        downloads = sum(op.step_id.startswith(prefixes) for op in self.ledger.list_operations(plan.task_id))
        queries = json.loads(self.store.read(request.input.query_ref))["queries"]
        query = queries[int(route.logical_query_id.removeprefix("query-")) - 1]
        terms = re.findall(r"[\w]+", query.casefold())
        for candidate in receipt.candidates:
            if downloads >= request.limits.downloads_per_query:
                break
            # This deterministic filter uses only provider-declared relevance;
            # image semantics/quality remain the responsibility of the real VLM.
            if metadata_rejection(candidate.title, candidate.description, terms) is not None:
                continue
            downloads += 1
            binding = UIStepBinding(
                task_id=plan.task_id,
                step_id=f"search.download.{route.attempt_no}.{downloads}",
                capability="ui.search",
                inputs=[index],
                parameters_ref=self.store.put(
                    plan.task_id,
                    "acquisition",
                    canonical_json(
                        {"candidate_id": candidate.candidate_id, "logical_query_id": route.logical_query_id}
                    ).encode(),
                    role="download_parameters",
                ),
            )
            operation = self.ledger.reserve(plan, binding.step_id, 0, Cost(), ui_binding=binding, ui_request=request)
            if not self.ledger.begin_submit(operation.operation_id):
                raise OutcomeUnknown()
            try:
                with private_http():
                    data, media_type, _ = self.resolver._fetch_https(candidate.original_url)
                _, width, height, media_type = validate_ui_image(data, media_type, request.resources)
                if (
                    width < criteria.minimum_width
                    or height < criteria.minimum_height
                    or not criteria.minimum_aspect_ratio <= width / height <= criteria.maximum_aspect_ratio
                ):
                    raise PipelineError("image_criteria")
                sha = hashlib.sha256(data).hexdigest()
                with Image.open(BytesIO(data)) as image:
                    contrast = ImageStat.Stat(image.convert("L").resize((128, 128))).stddev[0]
                    if contrast < criteria.minimum_contrast:
                        raise PipelineError("insufficient_contrast")
                    phash = perceptual_hash(image)
                if sha in seen_sha or any(
                    (int(phash, 16) ^ int(previous, 16)).bit_count() <= 6 for previous in seen_phash
                ):
                    raise PipelineError("duplicate_candidate")
                seen_sha.add(sha)
                seen_phash.append(phash)
                original = self.store.put(
                    plan.task_id,
                    operation.operation_id,
                    data,
                    role="original",
                    media_type=media_type,
                    source_ids=[index.artifact_id],
                )
                provenance = self.store.put(
                    plan.task_id,
                    operation.operation_id,
                    canonical_json(
                        {
                            "schema_version": 1,
                            "origin": "search",
                            "acquisition_method": "search",
                            "search_backend": route.provider,
                            "acquired_at": datetime.now(UTC).isoformat(),
                            "source_page": candidate.source_page,
                            "license_status": "unknown",
                            "user_declared": {},
                            "provider_declared": {
                                "title": candidate.title,
                                "description": candidate.description,
                                "width": candidate.declared_width,
                                "height": candidate.declared_height,
                                "relevance": "query_term_match",
                                "image_semantics_verified": False,
                            },
                            "observed": {
                                "sha256": sha,
                                "width": width,
                                "height": height,
                                "media_type": media_type,
                                "phash": phash,
                                "phash_version": "dct-64-v1",
                                "contrast_stddev": contrast,
                            },
                            "route": route.model_dump(mode="json"),
                        }
                    ).encode(),
                    role="provenance",
                    source_ids=[original.artifact_id, index.artifact_id],
                )
                self.ledger.finish_ui(
                    operation.operation_id,
                    Cost(),
                    {"artifacts": [ref.model_dump(mode="json") for ref in (original, provenance)]},
                )
                return True
            except Exception:
                # Downloads do not create paid work; a completed failed download
                # has known zero monetary/GPU cost and keeps its failed entry.
                self.ledger.finish_ui(
                    operation.operation_id, Cost(), {"error_code": "candidate_unavailable"}, failed=True
                )
        return False
