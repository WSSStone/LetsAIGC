"""Quota-only data in the common ledger. Money is always stored in operations."""

import hashlib
import json
from uuid import uuid4

from ..pipelines.errors import PipelineError
from ..schemas.pipeline import Cost, canonical_json, digest
from .search import QuotaObservation, RouteDecision, choose_route


class QuotaLedger:
    def __init__(self, ledger):
        self.ledger = ledger
        with ledger.transaction() as db:
            if db.execute("PRAGMA user_version").fetchone()[0] not in {3, 4, 5}:
                raise PipelineError("migration_required", "Stop writers and migrate the ledger to v3")

    def scope(self, provider, account_alias, credential):
        if provider not in {"serpapi", "tavily"} or not credential:
            raise PipelineError("missing_credentials")
        from pydantic import TypeAdapter

        from ..schemas.pipeline import Identifier

        account_alias = TypeAdapter(Identifier).validate_python(account_alias)
        credential_hash = hashlib.sha256(credential.encode()).hexdigest()
        with self.ledger.transaction() as db:
            row = db.execute(
                "SELECT * FROM quota_scopes WHERE provider=? AND account_alias=?", (provider, account_alias)
            ).fetchone()
            if row:
                if row["credential_hash"] != credential_hash:
                    db.execute(
                        "UPDATE quota_scopes SET credential_hash=?,generation=generation+1 WHERE scope_id=?",
                        (credential_hash, row["scope_id"]),
                    )
                return row["scope_id"]
            scope_id = "scope-" + uuid4().hex
            db.execute(
                "INSERT INTO quota_scopes(scope_id,provider,account_alias,credential_hash) VALUES(?,?,?,?)",
                (scope_id, provider, account_alias, credential_hash),
            )
            return scope_id

    def snapshot(self, observation):
        observation = QuotaObservation.model_validate(observation.model_dump(mode="json"))
        with self.ledger.transaction() as db:
            scope = db.execute("SELECT * FROM quota_scopes WHERE scope_id=?", (observation.scope_id,)).fetchone()
            if not scope or scope["provider"] != observation.provider:
                raise PipelineError("quota_scope")
            identity = "snapshot-" + digest([observation.model_dump(mode="json"), scope["generation"]])[:40]
            db.execute(
                "INSERT OR IGNORE INTO quota_snapshots VALUES(?,?,?,?,?)",
                (
                    identity,
                    observation.scope_id,
                    scope["generation"],
                    observation.observed_at,
                    canonical_json(observation),
                ),
            )
            return identity

    @staticmethod
    def _available(db, scopes, now):
        observations, identities = {}, {}
        for provider, scope_id in scopes.items():
            row = db.execute(
                """SELECT s.* FROM quota_snapshots s JOIN quota_scopes q ON q.scope_id=s.scope_id
                AND q.generation=s.generation WHERE s.scope_id=? AND q.provider=?
                ORDER BY s.observed_at DESC,s.rowid DESC LIMIT 1""",
                (scope_id, provider),
            ).fetchone()
            if not row:
                continue
            observation = QuotaObservation.model_validate_json(row["payload"])
            if observation.status not in {"known", "exhausted"} or not 0 <= now - observation.observed_at < 300:
                continue
            failures = db.execute(
                "SELECT o.result FROM quota_reservations q JOIN operations o "
                "ON q.operation_id=o.operation_id WHERE q.scope_id=?",
                (scope_id,),
            ).fetchall()
            cooldown = observation.cooldown_until
            unavailable = False
            for failure in failures:
                result = json.loads(failure[0])
                cooldown = max(cooldown, result.get("cooldown_until", 0))
                unavailable |= (
                    result.get("error_code") in {"auth_failed", "quota_exhausted", "payg_limit"}
                    and result.get("settled_at", 0) >= observation.observed_at
                )
            if unavailable:
                continue
            # A newer timestamp is never proof that particular local charges
            # were covered. Unknown and unsettled usage remain deducted.
            held = db.execute(
                "SELECT COALESCE(SUM(units),0) FROM quota_reservations WHERE scope_id=? AND covered_watermark IS NULL",
                (scope_id,),
            ).fetchone()[0]
            remaining = max(0, observation.remaining - held)
            observations[provider] = observation.model_copy(
                update={
                    "remaining": remaining,
                    "status": "known" if remaining else "exhausted",
                    "cooldown_until": cooldown,
                }
            )
            identities[provider] = row["snapshot_id"]
        return observations, identities

    def available(self, scopes, *, now):
        with self.ledger.transaction() as db:
            return self._available(db, scopes, now)[0]

    def route(self, operation_id):
        with self.ledger.transaction() as db:
            row = db.execute("SELECT payload FROM quota_routes WHERE operation_id=?", (operation_id,)).fetchone()
        if row is None:
            raise PipelineError("route_not_found")
        return RouteDecision.model_validate_json(row[0])

    def reserve_search(self, plan, request, binding, *, logical_query_id, policy, scopes, prices, now):
        def admit(db, key):
            rows = db.execute(
                "SELECT * FROM quota_routes WHERE root_task_id=? ORDER BY rowid", (plan.task_id,)
            ).fetchall()
            if len(rows) >= min(policy.max_attempts, request.limits.search_attempts):
                raise PipelineError("search_attempt_limit")
            queries = {row["logical_query_id"] for row in rows} | {logical_query_id}
            if len(queries) > request.limits.queries:
                raise PipelineError("query_limit")
            observations, identities = self._available(db, scopes, now)
            remaining_money = (
                plan.envelope.budget.max_total_cost_usd
                - db.execute(
                    "SELECT COALESCE(SUM(actual_cost+reserved_cost),0) FROM operations WHERE task_id=?", (plan.task_id,)
                ).fetchone()[0]
                / 1_000_000
            )
            excluded = {
                provider
                for provider in scopes
                if provider not in prices
                or not prices[provider].ready("search")
                or prices[provider].search_upper_usd > min(remaining_money, plan.envelope.budget.max_iteration_cost_usd)
            }
            provider, reason = choose_route(
                policy, observations, now=now, current=rows[-1]["provider"] if rows else None, excluded=excluded
            )
            sequence = [row["provider"] for row in rows] + [provider]
            if sum(a != b for a, b in zip(sequence, sequence[1:], strict=False)) > min(
                policy.max_switches, request.limits.provider_switches
            ):
                raise PipelineError("provider_switch_limit")
            route = RouteDecision(
                task_id=plan.task_id,
                operation_id=key,
                logical_query_id=logical_query_id,
                attempt_no=len(rows) + 1,
                provider=provider,
                scope_id=scopes[provider],
                snapshot_id=identities[provider],
                policy_hash=digest(policy),
                reason=reason,
            )
            db.execute(
                "INSERT INTO quota_routes VALUES(?,?,?,?,?,?)",
                (key, plan.task_id, logical_query_id, route.attempt_no, provider, canonical_json(route)),
            )
            db.execute(
                "INSERT INTO quota_reservations VALUES(?,?,?,?,1,'held',NULL)",
                (key, route.scope_id, route.snapshot_id, observations[provider].unit),
            )
            return Cost(cost_usd=prices[provider].search_upper_usd)

        operation = self.ledger.reserve(
            plan, binding.step_id, binding.revision, Cost(), ui_binding=binding, ui_request=request, admission=admit
        )
        return operation, self.route(operation.operation_id)

    def reserve_probe(self, plan, request, binding, *, provider, scope_id, price, policy, now):
        if provider not in policy.allowed_providers or not price.ready("probe"):
            raise PipelineError("pricing_unavailable")

        def admit(db, key):
            scope = db.execute("SELECT provider FROM quota_scopes WHERE scope_id=?", (scope_id,)).fetchone()
            if not scope or scope[0] != provider:
                raise PipelineError("quota_scope")
            probes = db.execute(
                "SELECT * FROM quota_probes WHERE scope_id=? ORDER BY started_at DESC", (scope_id,)
            ).fetchall()
            if any(row["state"] != "settled" for row in probes):
                raise PipelineError("probe_in_progress", "probe_in_progress")
            task_count = db.execute(
                "SELECT COUNT(*) FROM quota_probes WHERE root_task_id=? AND provider=?", (plan.task_id, provider)
            ).fetchone()[0]
            if task_count >= policy.max_probes_per_provider:
                raise PipelineError("probe_limit", "probe_limit")
            if probes and now - probes[0]["started_at"] < policy.refresh_seconds:
                raise PipelineError("probe_rate_limited", "probe_rate_limited")
            if (
                provider == "tavily"
                and sum(0 <= now - row["started_at"] < 600 for row in probes) >= policy.probes_per_ten_minutes
            ):
                raise PipelineError("probe_rate_limited", "probe_rate_limited")
            db.execute(
                "INSERT INTO quota_probes VALUES(?,?,?,?,?,'held')", (key, plan.task_id, scope_id, provider, now)
            )
            return Cost(cost_usd=price.probe_upper_usd)

        return self.ledger.reserve(
            plan, binding.step_id, binding.revision, Cost(), ui_binding=binding, ui_request=request, admission=admit
        )
