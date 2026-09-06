"""Deterministic CPU projections of a frozen human draft; no inference imports."""

import hashlib
import json
import os
from contextlib import contextmanager
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw
from pydantic import TypeAdapter

from ..pipelines.errors import PipelineError
from ..schemas.pipeline import ArtifactRef, Identifier
from .normalize import png

PROJECTION_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


@contextmanager
def confirmation_lock(store, task_id):
    TypeAdapter(Identifier).validate_python(task_id)
    directory = (store.root / task_id).resolve()
    if not directory.is_relative_to(store.root):
        raise PipelineError("artifact_scope")
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / ".review-confirm.lock").open("a+b") as handle:
        try:
            if os.name == "nt":
                import msvcrt

                handle.seek(0)
                handle.write(b"0")
                handle.flush()
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise PipelineError("review_confirm_busy") from exc
        try:
            yield
        finally:
            if os.name == "nt":
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def materialize(repo, task_id, request):
    document = repo.document(task_id, request.draft_revision)
    head = repo.head(task_id)
    base = head["base_refs"]
    for value in base.values():
        base_ref = ArtifactRef.model_validate(value)
        if base_ref.task_id != task_id:
            raise PipelineError("artifact_scope")
        repo.store.read(base_ref)
    canonical = ArtifactRef.model_validate(base["canonical_ref"])
    if canonical.task_id != task_id:
        raise PipelineError("artifact_scope")
    reusable = {}
    if "base_crop_index_ref" in base:
        index = json.loads(repo.store.read(ArtifactRef.model_validate(base["base_crop_index_ref"])))
        reusable.update({tuple(c["bbox"]): c["ref"] for c in index["crops"]})
    with repo.ledger.transaction() as db:
        rows = db.execute(
            "SELECT patch_ref,published_refs FROM ui_review_revisions WHERE task_id=? AND revision<=? "
            "ORDER BY revision",
            (task_id, request.draft_revision),
        ).fetchall()
    patches = [json.loads(row["patch_ref"]) for row in rows if row["patch_ref"]]
    for row in rows:
        if row["published_refs"]:
            old = json.loads(row["published_refs"])
            manifest = json.loads(repo.store.read(ArtifactRef.model_validate(old["manifest_ref"])))
            if manifest["base_refs"]["canonical_ref"] == base["canonical_ref"]:
                reusable.update({tuple(c["bbox"]): c["ref"] for c in manifest["crops"]})
    if repo.resources:
        from .resources import check_storage

        pixels = sum(
            (e.bbox[2] - e.bbox[0]) * (e.bbox[3] - e.bbox[1])
            for e in document.elements
            if tuple(e.bbox) not in reusable
        )
        try:
            check_storage(
                repo.store,
                task_id,
                repo.resources,
                extra_bytes=pixels * 5 + document.width * document.height * 5,
                memory_bytes=document.width * document.height * 20,
            )
        except PipelineError as exc:
            raise PipelineError("review_storage_insufficient") from exc
    with Image.open(BytesIO(repo.store.read(canonical))) as original:
        image = original.copy()
    if image.size != (document.width, document.height):
        raise PipelineError("review_input_unavailable")
    overlay = image.convert("RGB")
    drawing = ImageDraw.Draw(overlay)
    crops = []
    operation = "review-confirm-" + hashlib.sha256(request.request_id.encode()).hexdigest()
    for index, element in enumerate(document.elements, 1):
        box = element.bbox
        ref = reusable.get(tuple(box))
        if ref:
            ref = ArtifactRef.model_validate(ref)
            if ref.task_id != task_id:
                raise PipelineError("artifact_scope")
            repo.store.read(ref)
        else:
            ref = repo.put(task_id, operation, png(image.crop(box)), "rect_crop", "image/png")
        crops.append(
            {
                "element_id": element.element_id,
                "bbox": box,
                "ref": ref.model_dump(mode="json"),
                "epistemic_status": "observed",
            }
        )
        color = {"text": "#44e0bd", "image": "#f5be65", "container": "#88aaff", "other": "#ef91cf"}[element.base_type]
        drawing.rectangle((box[0], box[1], box[2] - 1, box[3] - 1), outline=color, width=2)
        drawing.text(
            (box[0] + 2, box[1] + 2), f"{index} {element.base_type}", fill="white", stroke_width=1, stroke_fill="black"
        )
    refs = {
        "layout_ref": repo.put(task_id, operation, document, "review_layout"),
        "texts_ref": repo.put(
            task_id,
            operation,
            {"schema_version": 2, "task_id": task_id, "texts": [t.model_dump(mode="json") for t in document.texts]},
            "review_texts",
        ),
        "overlay_ref": repo.put(task_id, operation, png(overlay), "review_overlay", "image/png"),
    }
    manifest = {
        "schema_version": 2,
        "kind": "ui_review",
        "task_id": task_id,
        "review_revision": request.draft_revision,
        "layout_origin": "human",
        "evaluation_lane": "human_assisted",
        "external_calls": 0,
        "actor_source": "local_human",
        "created_at": datetime.now(UTC).isoformat(),
        "taxonomy_version": 1,
        "projection_version": 1,
        "projection_sha256": PROJECTION_SHA256,
        "base_refs": base,
        "patch_refs": patches,
        "crops": crops,
        "outputs": {k: v.model_dump(mode="json") for k, v in refs.items()},
        "production_export_approved": False,
        "quality_status": "pending",
    }
    for ref in [
        canonical,
        *refs.values(),
        *[ArtifactRef.model_validate(c["ref"]) for c in crops],
        *[ArtifactRef.model_validate(p) for p in patches],
    ]:
        repo.store.read(ref)
    refs["manifest_ref"] = repo.put(task_id, operation, manifest, "review_manifest")
    repo.store.read(refs["manifest_ref"])
    return {k: v.model_dump(mode="json") for k, v in refs.items()}


def export_review(repo, task_id, revision, destination):
    """Copy one verified confirmed snapshot for inspection; never a production export."""
    binding = repo.binding(task_id, revision)
    manifest = json.loads(repo.store.read(binding.review_manifest_ref))
    files = {
        "layout.json": binding.layout_ref,
        "texts.json": binding.texts_ref,
        "canonical.png": binding.canonical_ref,
        "manifest.json": binding.review_manifest_ref,
        "overlay.png": ArtifactRef.model_validate(manifest["outputs"]["overlay_ref"]),
    }
    for index, crop in enumerate(manifest["crops"], 1):
        files[f"crops/{index:04d}.png"] = ArtifactRef.model_validate(crop["ref"])
    for ref in files.values():
        if ref.task_id != task_id:
            raise PipelineError("artifact_scope")
        repo.store.read(ref)
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=False)
    for name, ref in files.items():
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(repo.store.read(ref))
    (destination / "binding.json").write_text(binding.model_dump_json(indent=2), encoding="utf-8")
    return binding
