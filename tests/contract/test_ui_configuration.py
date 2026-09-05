import pytest

from letsaigc.config import get_url_setting
from letsaigc.errors import ValidationError


def test_mistaken_secret_in_endpoint_is_not_echoed(monkeypatch):
    secret = "synthetic-secret-not-an-endpoint"
    monkeypatch.setenv("LLM_BASE_URL", secret)
    with pytest.raises(ValidationError) as raised:
        get_url_setting("LLM_BASE_URL")
    assert secret not in str(raised.value)
    assert secret not in repr(raised.value.__cause__)


def test_ui_doctor_reports_safe_actionable_reasons(monkeypatch):
    from letsaigc.execution.temporal import config as temporal_config
    from letsaigc.pipelines.errors import PipelineError
    from letsaigc.ui_analysis import runtime
    from letsaigc.vision import ocr

    monkeypatch.setattr(runtime, "vision_client", lambda: (_ for _ in ()).throw(PipelineError("vision_unavailable")))
    monkeypatch.setattr(
        ocr,
        "validate_model_files",
        lambda *_: (_ for _ in ()).throw(PipelineError("model_not_ready")),
    )
    monkeypatch.setattr(runtime, "endpoint_fingerprint", lambda: (_ for _ in ()).throw(ValidationError("invalid")))
    monkeypatch.setattr(runtime, "load_llm_api_key", lambda: None)
    monkeypatch.setattr(runtime, "get_setting", lambda name, default=None: default)
    monkeypatch.setattr(
        temporal_config,
        "diagnose",
        lambda: {"status": "warn", "sdk_version": "1.32.0", "service_reachable": False},
    )
    status = runtime.diagnose()
    assert status["ocr"]["reasons"] == [
        "model_lock_unverified",
        "service_unreachable",
        "authentication_not_configured",
    ]
    assert "endpoint_invalid" in status["vlm"]["reasons"]
    assert status["temporal"]["reasons"] == ["service_unreachable"]
    for provider in ("serpapi", "tavily"):
        assert "credentials_not_configured" in status["search"]["providers"][provider]["reasons"]
        assert "search_pricing_unverified" in status["search"]["providers"][provider]["reasons"]
    serialized = str(status)
    assert "LLM_BASE_URL" not in serialized and "API_KEY" not in serialized
