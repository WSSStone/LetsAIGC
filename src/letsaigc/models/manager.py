from __future__ import annotations

import shutil
from pathlib import Path
from urllib.parse import quote

import httpx

from ..config import get_setting, load_catalog
from ..errors import ReadinessError, ValidationError
from ..paths import local_path
from ..policy import assert_model_allowed, verify_sha256
from ..schemas import Catalog, ModelEntry


class ModelManager:
    def __init__(self, catalog: Catalog | None = None, root: Path | None = None) -> None:
        self.catalog = catalog or load_catalog()
        self.root = root or local_path("models")

    def list(self) -> list[dict]:
        return [
            {
                "id": model.id,
                "lane": model.license.lane.value,
                "gated": model.gated,
                "enabled_by_default": model.enabled_by_default,
                "installed": all((self.root / file.target_path).is_file() for file in model.files),
            }
            for model in self.catalog.models
        ]

    def models_for_profile(self, profile: str) -> list[ModelEntry]:
        try:
            ids = self.catalog.profiles[profile]
        except KeyError as exc:
            raise ValidationError(f"Unknown model profile: {profile}") from exc
        lookup = self.catalog.by_id()
        return [lookup[model_id] for model_id in ids]

    def verify_model(self, model: ModelEntry) -> list[dict]:
        results = []
        for item in model.files:
            target = self.root / item.target_path
            if not target.is_file():
                results.append({"path": str(target), "ok": False, "reason": "missing"})
                continue
            actual = verify_sha256(target, item.sha256)
            results.append({"path": str(target), "ok": True, "sha256": actual})
        return results

    def verify_profile(self, profile: str) -> list[dict]:
        return [
            {"model": model.id, "files": self.verify_model(model)}
            for model in self.models_for_profile(profile)
        ]

    def sync(self, profile: str, *, token: str | None = None) -> list[dict]:
        models = self.models_for_profile(profile)
        required = sum(file.size_bytes for model in models for file in model.files)
        self.root.mkdir(parents=True, exist_ok=True)
        free = shutil.disk_usage(self.root).free
        if free < required + 5 * 1024**3:
            raise ReadinessError(
                "Insufficient free disk for model profile",
                details={"required_bytes": required, "free_bytes": free},
            )
        results = []
        for model in models:
            assert_model_allowed(model, operation="sync")
            for item in model.files:
                target = self.root / item.target_path
                target.parent.mkdir(parents=True, exist_ok=True)
                if target.is_file():
                    verify_sha256(target, item.sha256)
                    results.append({"model": model.id, "path": str(target), "status": "verified"})
                    continue
                part = target.with_suffix(target.suffix + ".part")
                url = (
                    f"https://huggingface.co/{model.source.repo}/resolve/"
                    f"{model.source.revision}/{quote(item.source_path)}?download=true"
                )
                auth_token = token or get_setting("HF_TOKEN")
                headers = {"Authorization": f"Bearer {auth_token}"} if auth_token else {}
                try:
                    with httpx.stream("GET", url, headers=headers, follow_redirects=True, timeout=None) as response:
                        response.raise_for_status()
                        with part.open("wb") as handle:
                            for chunk in response.iter_bytes(8 * 1024 * 1024):
                                handle.write(chunk)
                    verify_sha256(part, item.sha256)
                    part.replace(target)
                except Exception:
                    part.unlink(missing_ok=True)
                    raise
                results.append({"model": model.id, "path": str(target), "status": "downloaded"})
        return results
