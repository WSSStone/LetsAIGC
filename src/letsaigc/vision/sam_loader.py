"""Safe, local-only SAM2 model loading and preprocessing contracts.

This module deliberately stops at model/runtime preparation.  Prompt
interpretation and mask/alpha/glyph derivation are implemented by T023.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
from pathlib import Path
from typing import Any

import yaml

from ..paths import find_repo_root
from ..pipelines.errors import PipelineError


def load_sam_settings(root: Path | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    """Load the segmentation endpoint and its platform-independent lock."""

    root = root or find_repo_root()
    runtime = yaml.safe_load((root / "configs/runtime/vision.yaml").read_text(encoding="utf-8"))
    config = runtime["segmentation"]
    lock_path = root / config["lock"]
    lock = yaml.safe_load(lock_path.read_text(encoding="utf-8"))
    if lock.get("schema_version") != 1 or lock.get("protocol_version") != 1:
        raise PipelineError("model_not_ready", "Unsupported SAM runtime lock")
    return config, lock


def _sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def validate_sam_model_files(lock: dict[str, Any], root: Path) -> Path:
    """Validate exact local files before importing Transformers or Torch.

    The directory is intentionally checked for an exact allow-list.  A
    safetensors extension alone is not considered proof of a safe checkpoint;
    pickle based checkpoints and unexpected files are rejected.
    """

    try:
        segmentation = lock["segmentation"]
        if segmentation["loader"] != "transformers-sam2-safetensors":
            raise ValueError("Unsupported SAM loader")
        if segmentation["device"] != "cuda" or not segmentation["local_files_only"]:
            raise ValueError("SAM must use local CUDA inference")
        if segmentation["trust_remote_code"]:
            raise ValueError("Remote code is not allowed")
        model = segmentation["model"]
        if model["format"] != "safetensors" or model["repo"] != "facebook/sam2.1-hiera-large":
            raise ValueError("Unexpected SAM model")
        if model["revision"] != "665f8e2ad61cf5f53d65644ff27c8ee525124610":
            raise ValueError("SAM revision is not pinned")
        if model["license"]["id"] != "Apache-2.0" or model["license"]["status"] != "verified":
            raise ValueError("SAM license is not verified")
        relative = Path(model["directory"])
        if relative.is_absolute():
            raise ValueError("SAM model path must be relative")
        model_dir = (root / relative).resolve(strict=True)
        if not model_dir.is_relative_to(root.resolve()):
            raise ValueError("SAM model path escapes repository root")
        files = model["files"]
        if not isinstance(files, dict) or "model.safetensors" not in files:
            raise ValueError("SAM model file records are incomplete")
        required_processor = model.get("required_processor_files", [])
        if (
            not isinstance(required_processor, list)
            or any(not isinstance(name, str) for name in required_processor)
            or any(name not in files for name in required_processor)
        ):
            raise ValueError("SAM processor/config file hashes are missing")
        expected = set(files)
        entries = list(model_dir.iterdir())
        actual = {path.name for path in entries if path.is_file()}
        if any(not path.is_file() for path in entries):
            raise ValueError("SAM local snapshot contains unexpected directories")
        if expected != actual:
            raise ValueError("SAM local snapshot is incomplete or contains unexpected files")
        if any(path.suffix.lower() in {".pt", ".pth", ".bin", ".ckpt", ".pickle"} for path in entries):
            raise ValueError("Pickle checkpoint fallback is prohibited")
        for name, record in files.items():
            target = (model_dir / name).resolve(strict=True)
            if not target.is_relative_to(model_dir) or record.get("format") == "pickle":
                raise ValueError("Unsafe SAM file")
            expected_hash = record.get("sha256")
            if not isinstance(expected_hash, str) or len(expected_hash) != 64:
                raise ValueError("SAM file hash is missing")
            if _sha256(target) != expected_hash:
                raise ValueError("SAM file hash mismatch")
        return model_dir
    except Exception:
        raise PipelineError("model_not_ready", "SAM requires the exact verified local safetensors snapshot") from None


def validate_sam_preprocessing(lock: dict[str, Any]) -> None:
    """Require the lock to describe the processor transform used by SAM2."""

    try:
        preprocessing = lock["segmentation"]["model"]["preprocessing"]
        if preprocessing.get("processor") != "Sam2ImageProcessor":
            raise ValueError("Unexpected SAM processor")
        if preprocessing.get("size") != {"height": 1024, "width": 1024}:
            raise ValueError("SAM processor size is not the locked 1024 square")
        if preprocessing.get("default_to_square") is not True:
            raise ValueError("SAM processor square policy is not locked")
        if preprocessing.get("do_pad") is not None or preprocessing.get("target_size") != 1024:
            raise ValueError("SAM processor padding policy is not locked")
    except Exception:
        raise PipelineError("model_not_ready", "SAM processor preprocessing lock is not satisfied") from None


def validate_loaded_sam_preprocessing(processor: Any, lock: dict[str, Any]) -> None:
    """Verify the effective transform on the loaded Transformers processor.

    ``Sam2Processor`` is a wrapper around ``Sam2ImageProcessorFast`` in the
    pinned Transformers release.  The lock describes the public transform,
    while this check reads the effective values from the wrapper and its
    image processor.  A valid local snapshot with a drifted processor config
    must remain unavailable before a model is moved to CUDA.
    """

    try:
        validate_sam_preprocessing(lock)
        expected = lock["segmentation"]["model"]["preprocessing"]
        image_processor = processor.image_processor
        actual_size = image_processor.size
        if actual_size != expected["size"]:
            raise ValueError("SAM loaded processor size drift")
        if image_processor.default_to_square is not expected["default_to_square"]:
            raise ValueError("SAM loaded processor square policy drift")
        # ``do_pad`` is absent on the pinned SAM2 image processor when no
        # padding is configured; absent and explicit null are equivalent here.
        if getattr(image_processor, "do_pad", None) != expected["do_pad"]:
            raise ValueError("SAM loaded processor padding policy drift")
        if processor.target_size != expected["target_size"]:
            raise ValueError("SAM loaded processor target size drift")
    except Exception:
        raise PipelineError("model_not_ready", "SAM loaded processor preprocessing is not locked") from None


def validate_sam_package_lock(lock: dict[str, Any]) -> None:
    """Verify package versions without importing heavyweight CUDA packages."""

    try:
        segmentation = lock["segmentation"]
        if segmentation.get("verification") != "verified":
            raise ValueError("SAM package verification is pending")
        packages = segmentation["packages"]
        for name, expected in packages.items():
            if importlib.metadata.version(name) != expected["version"]:
                raise ValueError(f"{name} version mismatch")
            if len(expected.get("wheel_sha256", "")) != 64:
                raise ValueError(f"{name} wheel hash missing")
    except Exception:
        raise PipelineError("model_not_ready", "SAM package lock is not satisfied") from None


def prepare_sam_view(width: int, height: int, *, max_edge: int = 1024, pad_to: int = 1) -> dict[str, Any]:
    """Return deterministic resize/pad and inverse mapping metadata.

    The locked SAM2 processor uses ``size={height: 1024, width: 1024}``,
    ``default_to_square=True`` and no SAM padding.  This is intentionally a
    square resize (including its anisotropic scale for non-square inputs), so
    this contract does not silently substitute a custom aspect-preserving
    transform that the engine would not use.
    """

    if type(width) is not int or type(height) is not int or width <= 0 or height <= 0:
        raise PipelineError("invalid_input", "SAM view dimensions must be positive integers")
    if type(max_edge) is not int or max_edge <= 0 or type(pad_to) is not int or pad_to <= 0:
        raise PipelineError("invalid_input", "SAM view limits must be positive integers")
    if pad_to != 1:
        raise PipelineError("invalid_input", "SAM2 processor does not use an external padding stride")
    resize_w = resize_h = max_edge
    scale_x = resize_w / width
    scale_y = resize_h / height
    return {
        "canonical_size": [width, height],
        "model_size": [resize_w, resize_h],
        "resize": [resize_w, resize_h],
        "pad": [0, 0, 0, 0],
        "scale": [scale_x, scale_y],
        "inverse_scale": [1 / scale_x, 1 / scale_y],
        "inverse": [[1 / scale_x, 0.0, 0.0],
                    [0.0, 1 / scale_y, 0.0],
                    [0.0, 0.0, 1.0]],
        "processor": {
            "size": {"height": max_edge, "width": max_edge},
            "default_to_square": True,
            "do_pad": None,
            "target_size": max_edge,
        },
        "mapping_version": "sam2-transformers-square-v1",
    }


class SAM2Engine:
    """Load the fixed Transformers SAM2 snapshot without remote fallback."""

    # T022 owns loading and release only.  T023 will set execution_ready after
    # it supplies prompt/mask inference; a loaded model is not an executable
    # segmentation provider by itself.
    loader_ready = False
    execution_ready = False

    def __init__(self, lock: dict[str, Any], root: Path):
        self.lock = lock
        self.root = root
        validate_sam_preprocessing(lock)
        self.model_dir = validate_sam_model_files(lock, root)
        validate_sam_package_lock(lock)
        try:
            import torch
            from transformers import Sam2Model, Sam2Processor
        except Exception as exc:
            raise PipelineError("model_not_ready", "SAM runtime packages are unavailable") from exc
        self._torch = torch
        try:
            self.processor = Sam2Processor.from_pretrained(
                str(self.model_dir), local_files_only=True, trust_remote_code=False
            )
            validate_loaded_sam_preprocessing(self.processor, lock)
            loaded = Sam2Model.from_pretrained(
                str(self.model_dir),
                local_files_only=True,
                use_safetensors=True,
                trust_remote_code=False,
                output_loading_info=True,
            )
            if not isinstance(loaded, tuple) or len(loaded) != 2:
                # A Transformers implementation that ignores
                # output_loading_info is unsafe: warnings must not hide drift.
                raise PipelineError("model_not_ready", "SAM loader did not return loading information")
            model, loading_info = loaded
            if not isinstance(loading_info, dict):
                raise PipelineError("model_not_ready", "SAM loading information is invalid")
            allowed_unused = set(self.lock["segmentation"].get("allowed_unused_keys", []))
            for field in ("missing_keys", "mismatched_keys", "unexpected_keys", "error_msgs"):
                raw_values = loading_info.get(field, [])
                if raw_values is None:
                    raw_values = []
                if isinstance(raw_values, (str, bytes, dict)):
                    raise PipelineError("model_not_ready", f"SAM {field} are invalid")
                try:
                    values = set(raw_values)
                except (TypeError, ValueError):
                    raise PipelineError("model_not_ready", f"SAM {field} are invalid") from None
                if field == "unexpected_keys":
                    values -= allowed_unused
                if values:
                    raise PipelineError("model_not_ready", f"SAM {field} are not reviewed")
            self.model = model.to("cuda")
            self.model.eval()
        except PipelineError:
            self.processor = None
            raise
        except Exception:
            self.processor = None
            raise PipelineError("model_not_ready", "SAM local model loading failed") from None
        self.loader_ready = True
        self.execution_ready = True

    def segment(self, store, job):
        """Run frozen SAM prompts and return a resumable asset bundle."""

        if not self.loader_ready or not self.execution_ready or self.model is None or self.processor is None:
            raise PipelineError("segmentation_not_ready", "SAM inference is not ready")
        from .segmentation import segment_sam_job

        return segment_sam_job(self, store, job)

    def release(self) -> bool:
        model = getattr(self, "model", None)
        self.model = None
        self.processor = None
        self.loader_ready = False
        self.execution_ready = False
        if model is not None:
            del model
        try:
            self._torch.cuda.empty_cache()
            return True
        except Exception:
            return False
