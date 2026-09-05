"""CPU Paddle adapter with explicit static local models and preserved OCR evidence."""

import hashlib
import math
import os
from importlib import metadata
from io import BytesIO
from pathlib import Path

import numpy as np
from PIL import Image

from ..pipelines.errors import PipelineError
from ..schemas.pipeline import digest
from ..schemas.ui import ImageView
from ..ui_analysis.coordinates import checked_polygon, iou, map_point


def _plain_ocr_value(value):
    """Convert only expected numeric/string OCR containers to JSON-native values."""
    if isinstance(value, np.ndarray):
        return _plain_ocr_value(value.tolist())
    if isinstance(value, np.generic):
        return _plain_ocr_value(value.item())
    if isinstance(value, (list, tuple)):
        return [_plain_ocr_value(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise ValueError("OCR output contains an unsupported value")


def validate_model_files(lock: dict, root: Path) -> dict[str, Path]:
    try:
        if lock["schema_version"] != 1 or lock["protocol_version"] != 1:
            raise ValueError("Unsupported lock")
        ocr = lock["ocr"]
        if ocr["device"] != "cpu" or ocr["loader"] != "paddle-static-inference":
            raise ValueError("Unsupported loader")
        paths = {}
        for role, expected_name in (("det", "PP-OCRv5_server_det"), ("rec", "PP-OCRv5_server_rec")):
            model = ocr["models"][role]
            if model["id"] != expected_name or model["license_status"] != "verified" or not model["revision"]:
                raise ValueError("Unverified model")
            if model["license_id"] != "Apache-2.0" or not model["license_evidence_sha256"]:
                raise ValueError("Unverified license")
            relative = Path(model["directory"])
            path = (root / relative).resolve(strict=True)
            if relative.is_absolute() or not path.is_relative_to(root.resolve()):
                raise ValueError("Unsafe model directory")
            files = model["files"]
            if set(files) != {"inference.json", "inference.pdiparams", "inference.yml"}:
                raise ValueError("Only reviewed static inference files are accepted")
            if {item.name for item in path.iterdir()} != set(files):
                raise ValueError("Unexpected model files; dynamic checkpoint fallback is prohibited")
            for name, sha in files.items():
                target = (path / name).resolve(strict=True)
                if not sha or len(sha) != 64 or not target.is_relative_to(path):
                    raise ValueError("Unverified model file")
                with target.open("rb") as stream:
                    actual = hashlib.file_digest(stream, "sha256").hexdigest()
                if actual != sha:
                    raise ValueError("Model changed")
            paths[role] = path
        return paths
    except Exception:
        raise PipelineError(
            "model_not_ready", "OCR requires verified local static files, package lock and license"
        ) from None


def validate_model_lock(lock: dict, root: Path) -> dict[str, Path]:
    paths = validate_model_files(lock, root)
    try:
        for package, expected in lock["ocr"]["packages"].items():
            if not expected["wheel_sha256"] or metadata.version(package) != expected["version"]:
                raise ValueError("Unverified package lock")
        return paths
    except Exception:
        raise PipelineError(
            "model_not_ready", "OCR requires verified local static files, package lock and license"
        ) from None


def normalize_ocr_result(raw: dict, view: ImageView, *, model_id: str, model_version: str) -> dict:
    values = {}
    for name in ("rec_texts", "rec_scores", "rec_polys", "dt_polys"):
        values[name] = _plain_ocr_value(raw[name])
        if not isinstance(values[name], list):
            raise ValueError("OCR response fields must be arrays")
    if not len(values["rec_texts"]) == len(values["rec_scores"]) == len(values["rec_polys"]):
        raise ValueError("OCR text, scores and recognized polygons must be paired")
    if len(values["rec_texts"]) > 4096 or len(values["dt_polys"]) > 4096:
        raise ValueError("OCR output exceeds bounded region count")
    for polygon in values["dt_polys"]:
        checked_polygon(polygon, view.width, view.height)
    texts = []
    for index, (text, score, polygon) in enumerate(
        zip(values["rec_texts"], values["rec_scores"], values["rec_polys"], strict=True)
    ):
        if (
            not isinstance(text, str)
            or len(text) > 16384
            or isinstance(score, bool)
            or not math.isfinite(score)
            or not 0 <= score <= 1
        ):
            raise ValueError("Invalid OCR text or confidence")
        checked_polygon(polygon, view.width, view.height)
        canonical = [map_point(point, view.inverse) for point in polygon]
        checked_polygon(canonical, view.crop[2], view.crop[3])
        texts.append(
            {
                "text_id": "text-" + digest([view.canonical_ref.sha256, view.view_id, index, text, canonical])[:32],
                "text": text,
                "score": float(score),
                "polygon": canonical,
                "view_id": view.view_id,
                "model_id": model_id,
                "model_version": model_version,
                "status": "low_confidence" if score < 0.5 else "observed",
                "merged_view_ids": [view.view_id],
            }
        )
    return {
        "schema_version": 1,
        "status": "observed" if texts else "empty",
        "texts": texts,
        "raw": values,
        "view_id": view.view_id,
        "canonical_sha256": view.canonical_ref.sha256,
    }


def merge_ocr_results(results: list[dict]) -> dict:
    texts, raw_refs = [], []
    identities = {result["canonical_sha256"] for result in results}
    if len(identities) != 1:
        raise ValueError("OCR results must describe the same canonical image")

    def box(item):
        xs, ys = zip(*item["polygon"], strict=True)
        return min(xs), min(ys), max(xs), max(ys)

    for result in results:
        raw_refs.append({"view_id": result["view_id"], "raw": result["raw"]})
        for text in result["texts"]:
            duplicate = next(
                (old for old in texts if old["text"] == text["text"] and iou(box(old), box(text)) >= 0.85), None
            )
            if duplicate:
                duplicate["merged_view_ids"] = sorted(set(duplicate["merged_view_ids"] + text["merged_view_ids"]))
            else:
                texts.append({**text, "merged_view_ids": list(text["merged_view_ids"])})
    return {
        "schema_version": 1,
        "status": "observed" if texts else "empty",
        "texts": texts,
        "raw_views": raw_refs,
        "canonical_sha256": next(iter(identities)),
    }


class PaddleOCREngine:
    def __init__(self, lock: dict, root: Path):
        self.lock, self.root = lock, root
        paths = validate_model_lock(lock, root)
        os.environ["PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK"] = "True"
        from paddleocr import PaddleOCR

        self.model = PaddleOCR(
            text_detection_model_name="PP-OCRv5_server_det",
            text_detection_model_dir=str(paths["det"]),
            text_recognition_model_name="PP-OCRv5_server_rec",
            text_recognition_model_dir=str(paths["rec"]),
            device="cpu",
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            use_textline_orientation=False,
            text_det_limit_side_len=1024,
            text_det_limit_type="max",
            text_rec_score_thresh=0.0,
            text_recognition_batch_size=1,
        )

    def recognize(self, store, job) -> dict:
        validate_model_lock(self.lock, self.root)
        view = ImageView.model_validate_json(store.read(job.view_ref))
        if view.input_ref.task_id != job.task_id or view.width > 1024 or view.height > 1024:
            raise PipelineError("invalid_input")
        with Image.open(BytesIO(store.read(view.input_ref))) as image:
            if image.size != (view.width, view.height):
                raise PipelineError("input_changed")
            pixels = np.asarray(image.convert("RGB"))[:, :, ::-1].copy()  # Paddle's ndarray input is BGR.
        results = self.model.predict(pixels)
        if len(results) != 1:
            raise PipelineError("invalid_output")
        return normalize_ocr_result(
            results[0],
            view,
            model_id="PP-OCRv5_server_rec",
            model_version=self.lock["ocr"]["models"]["rec"]["revision"],
        )

    def release(self):
        self.model = None
