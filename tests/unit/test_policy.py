from __future__ import annotations

from pathlib import Path

import pytest

from letsaigc.errors import PolicyError
from letsaigc.policy import evaluate_export, verify_sha256
from letsaigc.policy.gates import sha256_file
from letsaigc.schemas import LicenseLane, RunManifest
from letsaigc.schemas.models import RunGovernance, RunOutput


def test_hash_verification_rejects_mismatch(tmp_path: Path) -> None:
    path = tmp_path / "model.safetensors"
    path.write_bytes(b"safe bytes")
    with pytest.raises(PolicyError, match="SHA-256 mismatch"):
        verify_sha256(path, "0" * 64)
    assert verify_sha256(path, sha256_file(path)) == sha256_file(path)


def _manifest(*, approved: bool = False, lane: LicenseLane = LicenseLane.production) -> RunManifest:
    return RunManifest(
        run_id="test-run",
        kind="inference",
        status="succeeded",
        source={},
        parameters={},
        environment={},
        outputs=[RunOutput(path="out.png", sha256="a" * 64)],
        governance=RunGovernance(
            license_lanes=[lane],
            validations={"contract": True, "hashes": True, "provenance": True},
            human_approved=approved,
        ),
    )


def test_export_requires_human_approval() -> None:
    denied = evaluate_export(_manifest(), lane=LicenseLane.production)
    assert not denied.allowed
    assert "human review is not approved" in denied.reasons
    assert evaluate_export(_manifest(approved=True), lane=LicenseLane.production).allowed


def test_restricted_lane_never_exports() -> None:
    decision = evaluate_export(
        _manifest(approved=True, lane=LicenseLane.restricted),
        lane=LicenseLane.production,
    )
    assert not decision.allowed
    assert any("not in the production lane" in reason for reason in decision.reasons)


def test_dynamic_graph_is_rechecked_at_export(tmp_path: Path) -> None:
    path = tmp_path / "graph.json"
    path.write_text('{"changed": true}')
    manifest = _manifest(approved=True)
    manifest.source = {"compiled_graph_path": str(path), "compiled_graph_sha256": "0" * 64}
    decision = evaluate_export(manifest, lane=LicenseLane.production)
    assert not decision.allowed
    assert "compiled graph hash is invalid" in decision.reasons


def test_agent_parent_cannot_hide_experimental_child(monkeypatch) -> None:
    child = _manifest(approved=False)
    child.governance.validations["runtime_qualification"] = False
    parent = _manifest(approved=True)
    parent.kind = "agent_task"
    parent.outputs[0].derived_from_run_id = "child"
    monkeypatch.setattr("letsaigc.tracking.load_manifest", lambda run_id: child)
    decision = evaluate_export(parent, lane=LicenseLane.production)
    assert not decision.allowed
    assert any("selected source" in reason for reason in decision.reasons)
    assert not child.governance.human_approved
