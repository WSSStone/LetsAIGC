from __future__ import annotations

import base64
from pathlib import Path
from types import SimpleNamespace

from PIL import Image

from letsaigc.assets import AssetResolver
from letsaigc.backends.openai_image import OpenAIImageBackend
from letsaigc.schemas import ExecutionEnvelope, GenerationIntent, GenerationPlan, TaskBudget


class FakeImages:
    def __init__(self, payload: str) -> None:
        self.payload = payload
        self.calls: list[tuple[str, dict]] = []

    def generate(self, **kwargs):
        self.calls.append(("generate", kwargs))
        return SimpleNamespace(
            data=[SimpleNamespace(b64_json=self.payload)],
            usage={"input_tokens_details": {"text_tokens": 10, "image_tokens": 0}, "output_tokens": 20},
            _request_id="req-test",
        )

    def edit(self, **kwargs):
        recorded = kwargs | {"image": [Path(item.name).name for item in kwargs["image"]]}
        self.calls.append(("edit", recorded))
        return SimpleNamespace(
            data=[SimpleNamespace(b64_json=self.payload)],
            usage={"input_tokens_details": {"text_tokens": 10, "image_tokens": 30}, "output_tokens": 20},
            _request_id="req-edit",
        )


def _plan(*, intent=GenerationIntent.text_to_image, assets=None) -> GenerationPlan:
    budget = TaskBudget(
        max_total_cost_usd=1,
        max_iteration_cost_usd=0.3,
        max_total_gpu_minutes=0,
        max_iteration_gpu_minutes=0,
        max_revisions=0,
    )
    return GenerationPlan(
        task_id="task-openai",
        session_id="session-openai",
        intent=intent,
        user_intent="potion icon",
        backend="openai",
        model="gpt-image-2-2026-04-21",
        parameters={
            "prompt": "potion icon",
            "size": "1024x1024",
            "quality": "low",
            "background": "auto",
            "pricing_id": "openai-standard-2026-09-02",
        },
        input_assets=assets or [],
        acceptance_criteria=["centered"],
        envelope=ExecutionEnvelope(
            backend="openai",
            model="gpt-image-2-2026-04-21",
            max_width=1024,
            max_height=1024,
            quality="low",
            background="auto",
            allowed_tools=["openai.image"],
            mutable_parameters=["prompt"],
            budget=budget,
        ),
    )


def test_generate_uses_pinned_snapshot_and_records_usage(monkeypatch, tmp_path: Path) -> None:
    source = tmp_path / "provider.png"
    Image.new("RGB", (16, 16), "green").save(source)
    images = FakeImages(base64.b64encode(source.read_bytes()).decode())
    monkeypatch.setenv("LETSAIGC_ROOT", str(tmp_path))
    (tmp_path / "configs" / "providers").mkdir(parents=True)
    pricing = Path(__file__).parents[2] / "configs" / "providers" / "openai-pricing.yaml"
    (tmp_path / "configs" / "providers" / "openai-pricing.yaml").write_bytes(pricing.read_bytes())
    models = Path(__file__).parents[2] / "configs" / "providers" / "openai-models.yaml"
    (tmp_path / "configs" / "providers" / "openai-models.yaml").write_bytes(models.read_bytes())
    result = OpenAIImageBackend(SimpleNamespace(images=images)).execute(_plan(), iteration_id="iter-00")
    call, kwargs = images.calls[0]
    assert call == "generate"
    assert kwargs["model"] == "gpt-image-2-2026-04-21"
    assert result.request_id == "req-test"
    assert result.request_hash and len(result.request_hash) == 64
    assert result.outputs[0].is_file()


def test_edit_uses_verified_local_bytes(monkeypatch, tmp_path: Path) -> None:
    source = tmp_path / "source.png"
    Image.new("RGB", (16, 16), "blue").save(source)
    asset = AssetResolver().resolve(str(source), tmp_path / "inputs")
    images = FakeImages(base64.b64encode(source.read_bytes()).decode())
    monkeypatch.setenv("LETSAIGC_ROOT", str(tmp_path))
    (tmp_path / "configs" / "providers").mkdir(parents=True)
    root = Path(__file__).parents[2] / "configs" / "providers"
    for name in ("openai-pricing.yaml", "openai-models.yaml"):
        (tmp_path / "configs" / "providers" / name).write_bytes((root / name).read_bytes())
    result = OpenAIImageBackend(SimpleNamespace(images=images)).execute(
        _plan(intent=GenerationIntent.image_to_image, assets=[asset]),
        iteration_id="iter-00",
    )
    call, kwargs = images.calls[0]
    assert call == "edit"
    assert kwargs["image"] == [Path(asset.derived_path).name]
    assert result.request_id == "req-edit"
