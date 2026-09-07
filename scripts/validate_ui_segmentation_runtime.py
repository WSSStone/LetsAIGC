"""Plan, approve and execute the bounded T024 Windows SAM acceptance case.

The plan phase performs deterministic local image/artifact work only.  It
does not load Torch, start the vision service, grant approval or run SAM.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import time
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw

from letsaigc.config import get_setting
from letsaigc.pipelines.approval import approve
from letsaigc.pipelines.errors import OutcomeUnknown, PipelineError
from letsaigc.pipelines.service import PipelineService
from letsaigc.schemas.pipeline import ArtifactRef, Cost, canonical_json, digest
from letsaigc.schemas.ui import (
    UIAnalysisRequest,
    UIResourceLimits,
    UISegmentationRequest,
    UISelection,
    UIStepBinding,
)
from letsaigc.ui_analysis.text_assets import extract_glyph_assets
from letsaigc.vision.client import SAMOperationBackend, VisionClient
from letsaigc.vision.sam_loader import (
    load_sam_settings,
    prepare_sam_view,
    validate_sam_model_files,
)
from letsaigc.vision.service import validation_runtime_paths

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_VALIDATION_ROOT = "t024-windows/acceptance-state"
BASE_FIXTURE = ROOT / "tests/fixtures/ui_analysis/hud-en-landscape.png"
PROMPT = {
    "element_id": "health-panel",
    "box": [24, 24, 331, 113],
    "points": [[180.0, 70.0]],
}
ROOT_BUDGET = {
    "max_total_cost_usd": 0,
    "max_iteration_cost_usd": 0,
    "max_total_gpu_minutes": 5,
    "max_iteration_gpu_minutes": 5,
    "max_revisions": 0,
}
RUN_VERSION = 7
PROMPT_VERSION = f"sam-prompt-v{RUN_VERSION}"
RUN_BUDGET = {
    "max_total_cost_usd": 0,
    "max_iteration_cost_usd": 0,
    "max_total_gpu_minutes": 1,
    "max_iteration_gpu_minutes": 1,
    "max_revisions": 0,
}
RESOURCES = UIResourceLimits(
    max_image_bytes=25 * 1024**2,
    max_pixels=1_000_000,
    max_image_edge=1280,
    ram_bytes=8 * 1024**3,
    vram_bytes=10_000_000_000,
    minimum_free_disk_bytes=16 * 1024**3,
    temporary_bytes=2 * 1024**3,
    network_bytes=0,
    active_seconds=600,
)


def _paths(value: str) -> tuple[Path, Path, Path]:
    artifacts, provider = validation_runtime_paths(ROOT, value)
    state = artifacts.parent
    return state, provider, state.parent


def _sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _alpha_probe(destination: Path) -> bytes:
    with Image.open(BASE_FIXTURE) as loaded:
        image = loaded.convert("RGBA")
    # The transparent patch is inside the approved panel bbox.  It exists only
    # to measure the invariant that output alpha never exceeds source alpha.
    alpha = image.getchannel("A")
    ImageDraw.Draw(alpha).rectangle((27, 27, 33, 33), fill=0)
    image.putalpha(alpha)
    destination.parent.mkdir(parents=True, exist_ok=True)
    image.save(destination, format="PNG", optimize=False)
    return destination.read_bytes()


def _selection_preview(input_bytes: bytes, destination: Path) -> bytes:
    with Image.open(BytesIO(input_bytes)) as loaded:
        image = loaded.convert("RGBA")
    draw = ImageDraw.Draw(image)
    draw.rectangle(PROMPT["box"], outline=(255, 76, 76, 255), width=4)
    x, y = PROMPT["points"][0]
    draw.ellipse((x - 6, y - 6, x + 6, y + 6), fill=(255, 224, 64, 255))
    destination.parent.mkdir(parents=True, exist_ok=True)
    image.save(destination, format="PNG", optimize=False)
    return destination.read_bytes()


def plan(value: str) -> dict:
    state, _, evidence_root = _paths(value)
    service = PipelineService(state, ui_schema=5)
    input_path = evidence_root / "input/hud-en-landscape-alpha-probe.png"
    preview_path = evidence_root / "input/selection-preview.png"
    input_bytes = _alpha_probe(input_path)
    preview_bytes = _selection_preview(input_bytes, preview_path)
    sample_hash = hashlib.sha256(input_bytes).hexdigest()
    root_task_id = "t024-win-root-" + sample_hash[:16]

    source = service.artifacts.put(
        root_task_id,
        "input",
        input_bytes,
        role="original",
        media_type="image/png",
        source_ids=[],
    )
    root_plan = service.ui_plan(
        root_task_id,
        UIAnalysisRequest(
            input={"kind": "manual", "inputs": [source]},
            output_mode="decompose",
            selection_mode="deferred",
            budget=ROOT_BUDGET,
            resources=RESOURCES,
        ),
    )
    selection_value = UISelection(
        sources=[
            {
                "source_id": source.artifact_id,
                "original_sha256": source.sha256,
                "target_regions": [{"kind": "bbox", "xyxy": PROMPT["box"]}],
                "keep_elements": [],
                "remove_elements": [],
            }
        ]
    )
    selection_bytes = canonical_json(selection_value).encode()
    root_selection = service.artifacts.put(
        root_task_id,
        "selection",
        selection_bytes,
        role="selection",
        media_type="application/json",
        source_ids=[source.artifact_id],
    )
    preview_ref = service.artifacts.put(
        root_task_id,
        "selection",
        preview_bytes,
        role="selection_preview",
        media_type="image/png",
        source_ids=[source.artifact_id, root_selection.artifact_id],
    )

    _, lock = load_sam_settings(ROOT)
    validate_sam_model_files(lock, ROOT)
    child_seed = digest(
        [sample_hash, root_selection.sha256, digest(lock), PROMPT_VERSION, PROMPT]
    )
    child_task_id = "t024-win-sam-" + child_seed[:16]
    canonical_ref = service.artifacts.put(
        child_task_id,
        "input",
        input_bytes,
        role="canonical",
        media_type="image/png",
        source_ids=[source.artifact_id],
    )
    child_selection = service.artifacts.put(
        child_task_id,
        "input",
        selection_bytes,
        role="selection",
        media_type="application/json",
        source_ids=[root_selection.artifact_id],
    )
    model_snapshot_ref = service.artifacts.put(
        child_task_id,
        "model",
        canonical_json(lock).encode(),
        role="model_snapshot",
        media_type="application/json",
    )
    request = UISegmentationRequest(
        canonical_ref=canonical_ref,
        selection_ref=child_selection,
        selection_revision=0,
        selection_hash=child_selection.sha256,
        prompts=[PROMPT],
        model_snapshot_ref=model_snapshot_ref,
        prompt_version=PROMPT_VERSION,
        resources=RESOURCES,
        result_roles=["segmentation"],
    )
    request_ref = service.artifacts.put(
        child_task_id,
        "request",
        canonical_json(request).encode(),
        role="request",
        media_type="application/json",
        source_ids=[canonical_ref.artifact_id, child_selection.artifact_id, model_snapshot_ref.artifact_id],
    )
    child_plan = service.ui_child_plan(
        child_task_id,
        parent_task_id=root_task_id,
        purpose="segmentation",
        source_ids=[source.artifact_id],
        request_ref=request_ref,
        selection_ref=root_selection,
        selection_revision=0,
        budget=RUN_BUDGET,
    )
    parameters_hash = digest(
        {"prompt_version": request.prompt_version, "prompts": [PROMPT]}
    )
    binding = UIStepBinding(
        task_id=child_task_id,
        step_id="segment",
        capability="ui.segment",
        inputs=child_plan.inputs,
        selection_ref=child_selection,
        selection_revision=0,
        selection_hash=child_selection.sha256,
        dependency_hashes={
            "sam-model": model_snapshot_ref.sha256,
            "sam-parameters": parameters_hash,
        },
    )
    with service.ledger.transaction() as db:
        prior_rows = db.execute(
            "SELECT operation_id,actual_gpu FROM operations WHERE task_id<>?",
            (child_task_id,),
        ).fetchall()
    prior_operation_ids = {row["operation_id"] for row in prior_rows}
    prior_actual_gpu_minutes = sum(row["actual_gpu"] for row in prior_rows) / 1_000_000
    for historical_path in evidence_root.glob("attempt-*-release-unknown.json"):
        historical = json.loads(historical_path.read_text(encoding="utf-8"))
        historical_operation = historical["operation"]
        if historical_operation["operation_id"] not in prior_operation_ids:
            prior_actual_gpu_minutes += historical["provider"]["job"]["actual_gpu"]
            prior_operation_ids.add(historical_operation["operation_id"])
    result = {
        "schema_version": 1,
        "attempt": RUN_VERSION,
        "acceptance_scope": "development_fixed_sample",
        "formal_quality_acceptance": False,
        "validation_root": value,
        "root_task_id": root_task_id,
        "root_plan_fingerprint": root_plan.fingerprint,
        "task_id": child_task_id,
        "plan_fingerprint": child_plan.fingerprint,
        "input": {
            "base_fixture": str(BASE_FIXTURE),
            "base_fixture_sha256": _sha256(BASE_FIXTURE),
            "derived_alpha_probe": str(input_path),
            "derived_sha256": sample_hash,
            "source_ref": source.model_dump(mode="json"),
            "commercial_ui": False,
        },
        "preview": {
            "path": str(preview_path),
            "sha256": hashlib.sha256(preview_bytes).hexdigest(),
            "ref": preview_ref.model_dump(mode="json"),
        },
        "canonical_ref": canonical_ref.model_dump(mode="json"),
        "canonical_size": [1280, 720],
        "selection": {
            "revision": 0,
            "sha256": child_selection.sha256,
            "root_ref": root_selection.model_dump(mode="json"),
            "ref": child_selection.model_dump(mode="json"),
        },
        "prompt": PROMPT,
        "parameters_hash": parameters_hash,
        "model_snapshot_ref": model_snapshot_ref.model_dump(mode="json"),
        "model": {
            "repo": lock["segmentation"]["model"]["repo"],
            "revision": lock["segmentation"]["model"]["revision"],
            "snapshot_digest": model_snapshot_ref.sha256,
            "weight_sha256": lock["segmentation"]["model"]["files"]["model.safetensors"]["sha256"],
            "packages": lock["segmentation"]["packages"],
            "preprocessing": lock["segmentation"]["model"]["preprocessing"],
        },
        "request_ref": request_ref.model_dump(mode="json"),
        "request": {
            "selection_hash": request.selection_hash,
            "parameters_hash": parameters_hash,
            "resources": request.resources.model_dump(mode="json"),
        },
        "binding": binding.model_dump(mode="json"),
        "budget": RUN_BUDGET,
        "prior_actual_gpu_minutes": prior_actual_gpu_minutes,
        "aggregate_upper_bound": {
            "cost_usd": 0,
            "gpu_minutes": prior_actual_gpu_minutes + RUN_BUDGET["max_total_gpu_minutes"],
        },
        "limitations": [
            "Development fixture derived from a repository synthetic HUD; it is not commercial UI evidence.",
            "A small transparent patch was added solely to verify alpha preservation.",
            "This follow-up exact plan exists only to verify the repaired graceful-release path "
            "after earlier attempts returned resource_release_unknown.",
            "The result does not satisfy formal 24-case quality acceptance.",
        ],
    }
    evidence_root.mkdir(parents=True, exist_ok=True)
    (evidence_root / "plan.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return result


def approve_plan(value: str, fingerprint: str) -> dict:
    state, _, evidence_root = _paths(value)
    service = PipelineService(state, ui_schema=5)
    plan_value = json.loads((evidence_root / "plan.json").read_text(encoding="utf-8"))
    if fingerprint != plan_value["plan_fingerprint"]:
        raise PipelineError("plan_changed", "Approval fingerprint does not match T024 plan")
    child_plan = service.checked_plan(plan_value["task_id"], fingerprint)
    request = approve(service.ledger, child_plan.task_id, fingerprint)
    consumed = service.ledger.consume_approval(request)
    return {
        "task_id": child_plan.task_id,
        "plan_fingerprint": child_plan.fingerprint,
        "approval_request_id": request.request_id,
        "approved": consumed,
    }


def _artifact_image(store, value: dict) -> Image.Image:
    ref = ArtifactRef.model_validate(value)
    with Image.open(BytesIO(store.read(ref))) as loaded:
        loaded.load()
        return loaded.copy()


def execute(value: str, fingerprint: str) -> dict:
    state, provider_root, evidence_root = _paths(value)
    service = PipelineService(state, ui_schema=5)
    planned = json.loads((evidence_root / "plan.json").read_text(encoding="utf-8"))
    if fingerprint != planned["plan_fingerprint"]:
        raise PipelineError("plan_changed", "Execution fingerprint does not match T024 plan")
    child_plan = service.checked_plan(planned["task_id"], fingerprint)
    with service.ledger.transaction() as db:
        approved = db.execute(
            "SELECT approved,cancelled FROM tasks WHERE task_id=?", (child_plan.task_id,)
        ).fetchone()
    if not approved or not approved["approved"] or approved["cancelled"]:
        raise PipelineError("approval_required", "Consume the exact T024 approval before GPU startup")

    config, lock = load_sam_settings(ROOT)
    token = get_setting(config["token_env"], "")
    signing_key = get_setting(config["signing_key_env"], "")
    if not token or len(signing_key) < 32:
        raise PipelineError("vision_not_ready", "Vision authentication is not configured")
    client = VisionClient(token=token, endpoint=config["endpoint"])
    health = client.health()
    model_ref = ArtifactRef.model_validate(planned["model_snapshot_ref"])
    if (
        not isinstance(health, dict)
        or health.get("ready") is not True
        or health.get("model_digest") != model_ref.sha256
    ):
        raise PipelineError("model_not_ready", "Approved SAM service is not ready for this snapshot")
    backend = SAMOperationBackend(service.ledger, service.artifacts, client, signing_key=signing_key)
    service.backends["ui.segment"] = backend
    binding = UIStepBinding.model_validate(planned["binding"])
    try:
        operation = service.submit_step(
            child_plan,
            binding,
            Cost(cost_usd=0, gpu_minutes=planned["budget"]["max_iteration_gpu_minutes"]),
        )
    except Exception:
        # Health checks load the approved snapshot before reservation.  If a
        # coordinator gate rejects the operation, release that load before
        # preserving and re-raising the gate error.
        try:
            backend.release()
        except Exception:
            pass
        raise
    recovered = backend.recover(operation.operation_id)
    if recovered is None or recovered.request_id != operation.provider_request_id:
        raise OutcomeUnknown()
    with service.ledger.transaction() as db:
        owner = db.execute(
            "SELECT operation_id FROM resources WHERE resource='local-gpu'"
        ).fetchone()
    gpu_single_owner = owner is not None and owner["operation_id"] == operation.operation_id
    deadline = time.monotonic() + RESOURCES.active_seconds
    while time.monotonic() < deadline:
        operation = service.observe_step(child_plan, operation.operation_id)
        observation = operation.result.get("observation", {})
        if observation.get("state") in {"succeeded", "failed"}:
            break
        time.sleep(0.25)
    else:
        raise OutcomeUnknown()
    operation = service.collect_step(child_plan, operation.operation_id)
    if operation.state != "succeeded" or len(operation.result.get("artifacts", [])) != 1:
        raise PipelineError("segmentation_failed", "T024 SAM operation did not succeed")
    segmentation_ref = ArtifactRef.model_validate(operation.result["artifacts"][0])
    bundle = json.loads(service.artifacts.read(segmentation_ref))
    if len(bundle.get("items", [])) != 1:
        raise PipelineError("invalid_output", "T024 expects one frozen SAM prompt")
    item = bundle["items"][0]
    glyph = extract_glyph_assets(
        service.artifacts,
        ArtifactRef.model_validate(planned["canonical_ref"]),
        {
            "text_id": "health-label",
            "bbox": [36, 29, 260, 58],
            "score": 1.0,
            "status": "development_reference",
        },
        operation_id=operation.operation_id,
    )
    if glyph["status"] not in {"estimated", "low_confidence"}:
        raise PipelineError("invalid_output", "Development glyph extraction did not produce evidence")

    canonical = _artifact_image(service.artifacts, planned["canonical_ref"]).convert("RGBA")
    rect = _artifact_image(service.artifacts, item["rect_crop_ref"])
    contour = _artifact_image(service.artifacts, item["contour_mask_ref"])
    alpha = _artifact_image(service.artifacts, item["estimated_alpha_ref"])
    visible = _artifact_image(service.artifacts, item["visible_crop_ref"]).convert("RGBA")
    x0, y0, x1, y1 = item["bbox"]
    expected_crop = canonical.crop((x0, y0, x1, y1))
    transparent_preserved = all(
        output <= source
        for output, source in zip(
            visible.getchannel("A").tobytes(),
            expected_crop.getchannel("A").tobytes(),
            strict=True,
        )
    )
    release = backend.release()
    allocated = release.get("cuda_allocated_bytes")
    if allocated != 0:
        raise PipelineError("resource_release_unknown", "SAM release did not prove zero allocated CUDA bytes")
    with sqlite3.connect(provider_root / "receipts.sqlite") as db:
        provider_accept_count = db.execute(
            "SELECT COUNT(*) FROM jobs WHERE operation_id=?", (operation.operation_id,)
        ).fetchone()[0]
    with service.ledger.transaction() as db:
        remaining_owner = db.execute(
            "SELECT operation_id FROM resources WHERE resource='local-gpu'"
        ).fetchone()
    mapping = prepare_sam_view(canonical.width, canonical.height)
    packages = lock["segmentation"]["packages"]
    runtime = {
        **planned,
        "approved_fingerprint": fingerprint,
        "canonical_sha256": planned["canonical_ref"]["sha256"],
        "model": {
            **planned["model"],
            "torch": packages["torch"]["version"],
            "torchvision": packages["torchvision"]["version"],
            "transformers": packages["transformers"]["version"],
        },
        "inference_calls": len(planned["prompt"] and [planned["prompt"]]),
        "execution": {
            "operation_id": operation.operation_id,
            "provider_request_id": operation.provider_request_id,
            "state": operation.state,
            "actual": operation.actual.model_dump(mode="json"),
            "usage_verdict": operation.result["usage_verdict"],
            "provider_accept_count": provider_accept_count,
            "recovery_request_count": 1,
        },
        "mapping": mapping,
        "segmentation_ref": segmentation_ref.model_dump(mode="json"),
        "segmentation_status": bundle["status"],
        "glyph": {
            key: value.model_dump(mode="json") if isinstance(value, ArtifactRef) else value
            for key, value in glyph.items()
        },
        "release": {"acknowledged": release["released"], "cuda_allocated_bytes": allocated},
        "checks": {
            "approved_binding_exact": bundle["selection_hash"] == planned["selection"]["sha256"],
            "model_snapshot_exact": bundle["model_digest"] == planned["model"]["snapshot_digest"],
            "canonical_mapping_recorded": mapping["canonical_size"] == planned["canonical_size"],
            "rect_crop_original_pixels": rect.convert("RGBA").tobytes()
            == expected_crop.tobytes(),
            "contour_mask_role": contour.mode == "L" and contour.size == canonical.size,
            "estimated_alpha_role": alpha.mode == "L" and alpha.size == canonical.size,
            "transparent_pixels_preserved": transparent_preserved,
            "glyph_is_cpu_estimate": glyph["status"] in {"estimated", "low_confidence"},
            "sam_control_not_glyph": item["element_id"] != glyph["text_id"],
            "gpu_single_owner": gpu_single_owner,
            "gpu_owner_released": remaining_owner is None,
            "provider_acceptance_not_duplicated": provider_accept_count == 1,
            "actual_usage_settled": operation.actual.gpu_minutes > 0,
            "approved_budget_respected": operation.result["usage_verdict"] != "budget_exceeded",
            "release_confirmed": release["released"] is True and allocated == 0,
        },
    }
    for name in ("segmentation-runtime.json", "execution-audit.json"):
        (evidence_root / name).write_text(
            json.dumps(runtime, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    (evidence_root.parent / "segmentation-runtime.json").write_text(
        json.dumps(runtime, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return runtime


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["plan", "approve", "execute"])
    parser.add_argument("--validation-root", default=DEFAULT_VALIDATION_ROOT)
    parser.add_argument("--fingerprint")
    args = parser.parse_args()
    if args.command == "plan":
        result = plan(args.validation_root)
    else:
        if not args.fingerprint:
            parser.error("--fingerprint is required for approve and execute")
        result = (
            approve_plan(args.validation_root, args.fingerprint)
            if args.command == "approve"
            else execute(args.validation_root, args.fingerprint)
        )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
