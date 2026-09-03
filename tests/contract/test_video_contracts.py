from __future__ import annotations

from letsaigc.policy.gates import evaluate_export
from letsaigc.schemas import (
    LicenseLane,
    MediaOutputDeclaration,
    RunGovernance,
    RunManifest,
    TemporalBudget,
    WorkflowContract,
)


def test_manifest_v11_adds_media_kinds_without_breaking_v10() -> None:
    legacy = RunManifest.model_validate(
        {
            "schema_version": "1.0",
            "run_id": "legacy",
            "kind": "inference",
            "status": "succeeded",
            "source": {},
            "parameters": {},
            "environment": {},
            "outputs": [],
            "governance": {
                "license_lanes": ["production"],
                "validations": {"contract": True, "hashes": True, "provenance": True},
            },
        }
    )
    assert legacy.schema_version == "1.0"

    media = legacy.model_copy(update={"schema_version": "1.1", "kind": "video_generation"})
    assert RunManifest.model_validate(media).kind == "video_generation"


def test_video_contract_declares_output_and_temporal_budget() -> None:
    contract = WorkflowContract(
        schema_version=1,
        id="video-test",
        version="1.0.0",
        description="test",
        ui_workflow="workflows/ui/video-test.json",
        api_workflow="workflows/api/video-test.json",
        input_schema={"type": "object"},
        output_schema={"type": "object"},
        bindings={},
        defaults={},
        models=[],
        nodes=["SaveVideo"],
        resource_budget={
            "vram_gib": 10,
            "ram_gib": 32,
            "free_disk_gib": 10,
            "timeout_seconds": 600,
        },
        export_lanes=[LicenseLane.production],
        media_kind="video",
        outputs=[
            MediaOutputDeclaration(
                node_id="9",
                history_field="videos",
                role="primary_video",
                media_kind="video",
                allowed_extensions=[".mp4"],
            )
        ],
        temporal_budget=TemporalBudget(
            width=512,
            height=512,
            fps=16,
            frame_count=17,
            duration_seconds=2,
            temporary_disk_gib=5,
        ),
    )
    assert contract.outputs[0].role == "primary_video"
    assert contract.temporal_budget.frame_count == 17


def test_wan_contract_matches_native_save_video_history_shape() -> None:
    from letsaigc.config import load_workflow_contract

    contract = load_workflow_contract("wan21-t2v-smoke")
    assert contract.outputs[0].history_field == "images"
    assert contract.outputs[0].media_kind == "video"


def test_new_run_kinds_keep_governance() -> None:
    manifest = RunManifest(
        run_id="sprite-run",
        kind="sprite_pipeline",
        status="created",
        source={},
        parameters={},
        environment={},
        parent_run_id="video-run",
        governance=RunGovernance(
            license_lanes=[LicenseLane.production],
            validations={"contract": False, "hashes": False, "provenance": True},
        ),
    )
    assert manifest.parent_run_id == "video-run"


def test_experimental_runtime_qualification_blocks_production() -> None:
    manifest = RunManifest(
        run_id="experimental-video",
        kind="video_generation",
        status="succeeded",
        source={},
        parameters={},
        environment={},
        outputs=[{"path": "clip.mp4", "sha256": "a" * 64}],
        governance=RunGovernance(
            license_lanes=[LicenseLane.production],
            validations={
                "contract": True,
                "hashes": True,
                "provenance": True,
                "runtime_qualification": False,
            },
        ),
    )
    assert evaluate_export(manifest, lane=LicenseLane.production).allowed is False
