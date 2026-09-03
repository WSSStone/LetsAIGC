from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from ..config import load_license_policy
from ..errors import PolicyError
from ..paths import local_path
from ..schemas import LicenseLane, ModelEntry, RunManifest


def sha256_file(path: Path, chunk_size: int = 8 * 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_sha256(path: Path, expected: str) -> str:
    actual = sha256_file(path)
    if actual.lower() != expected.lower():
        raise PolicyError(
            f"SHA-256 mismatch for {path.name}",
            details={"expected": expected, "actual": actual},
        )
    return actual


def _attestation_path() -> Path:
    return local_path("state", "license-attestations.json")


def has_attestation(model: ModelEntry) -> bool:
    path = _attestation_path()
    if not path.is_file():
        return False
    records = json.loads(path.read_text(encoding="utf-8"))
    record = records.get(model.id, {}) if isinstance(records, dict) else {}
    return (
        record.get("license_id") == model.license.id
        and record.get("source_revision") == model.source.revision
        and bool(record.get("accepted_at"))
    )


def assert_model_allowed(model: ModelEntry, *, operation: str) -> None:
    policy = load_license_policy()
    if f".{model.format}" not in policy.safe_weight_extensions:
        raise PolicyError(f"Unsafe weight format denied: {model.format}")
    lane_rules = policy.lanes[model.license.lane]
    if model.license.lane is LicenseLane.restricted and operation == "sync":
        raise PolicyError(f"Restricted model is catalog-only for {operation}: {model.id}")
    if (model.gated or lane_rules.requires_attestation) and not has_attestation(model):
        raise PolicyError(
            f"Local license attestation required for gated model: {model.id}",
            details={"attestation_file": str(_attestation_path())},
        )
    allowed = {
        "inference": lane_rules.allow_inference,
        "train": lane_rules.allow_training,
        "export": lane_rules.allow_production_export,
    }.get(operation, True)
    if not allowed:
        raise PolicyError(f"License lane blocks {operation}: {model.license.lane.value}")


@dataclass(frozen=True)
class ExportDecision:
    allowed: bool
    reasons: list[str]


def evaluate_export(manifest: RunManifest, *, lane: LicenseLane) -> ExportDecision:
    policy = load_license_policy().production_export
    reasons: list[str] = []
    if lane not in policy.allowed_lanes:
        reasons.append("only the production export lane is supported")
    if manifest.status != policy.require_status:
        reasons.append("run status is not succeeded")
    if not manifest.outputs:
        reasons.append("run has no output hashes")
    if (
        policy.require_hashes
        or policy.require_contract_validation
        or policy.require_provenance
    ) and any(value is not True for value in manifest.governance.validations.values()):
        reasons.append("one or more validation gates failed")
    if any(item not in policy.allowed_lanes for item in manifest.governance.license_lanes):
        reasons.append("a model or adapter is not in the production lane")
    if policy.require_human_approval and not manifest.governance.human_approved:
        reasons.append("human review is not approved")
    # Dynamic Agent graphs are local artifacts, not a reason to trust stale flags.
    for kind in ("graph", "contract"):
        path = manifest.source.get(f"compiled_{kind}_path")
        digest = manifest.source.get(f"compiled_{kind}_sha256")
        if path or digest:
            try:
                payload = json.loads(Path(str(path)).read_text(encoding="utf-8"))
                canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                if hashlib.sha256(canonical.encode()).hexdigest() != digest:
                    reasons.append(f"compiled {kind} hash is invalid")
            except (OSError, ValueError):
                reasons.append(f"compiled {kind} evidence is missing or invalid")
    if manifest.kind == "agent_task":
        selected = {output.derived_from_run_id for output in manifest.outputs}
        if len(selected) != 1 or None in selected:
            reasons.append("Agent selection has no unambiguous source run")
        else:
            from ..tracking import load_manifest

            try:
                child = load_manifest(str(next(iter(selected))))
                if child.kind == "agent_task":
                    reasons.append("nested Agent selection cannot be exported directly")
                else:
                    # Human reviews the parent selection; no automated human approval
                    # is persisted to a child. All technical/qualification gates carry.
                    reviewed_child = child.model_copy(deep=True)
                    reviewed_child.governance.human_approved = manifest.governance.human_approved
                    child_decision = evaluate_export(reviewed_child, lane=lane)
                    reasons.extend(f"selected source: {reason}" for reason in child_decision.reasons)
            except (OSError, ValueError):
                reasons.append("Agent selected source manifest is missing or invalid")
    return ExportDecision(not reasons, reasons)
