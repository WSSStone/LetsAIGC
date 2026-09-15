import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from letsaigc.pipelines.errors import PipelineError

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/validate_ui_vlm_transport.py"
SPEC = importlib.util.spec_from_file_location("letsaigc_t030_vlm_probe", SCRIPT)
assert SPEC and SPEC.loader
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)


def _response():
    return SimpleNamespace(
        id="resp-minimal",
        status="completed",
        output_text="A solid color image.",
        output=[],
        usage={"input_tokens": 20, "output_tokens": 5},
    )


def test_minimal_vlm_probe_plans_offline_and_requires_exact_one_shot_approval(tmp_path, monkeypatch):
    monkeypatch.setattr(probe, "VALIDATION_PARENT", tmp_path.resolve())
    monkeypatch.setattr(probe, "endpoint_fingerprint", lambda: "e" * 64)
    monkeypatch.setattr(
        probe,
        "get_setting",
        lambda name, default=None: "gpt-5.6-luna" if name == "LLM_VLM_MODEL" else default,
    )
    calls = []

    class Raw:
        status_code = 200
        headers = {"x-request-id": "req-minimal"}

        @staticmethod
        def parse():
            return _response()

    def create(**kwargs):
        calls.append(kwargs)
        return Raw()

    client = SimpleNamespace(
        responses=SimpleNamespace(with_raw_response=SimpleNamespace(create=create))
    )
    planned = probe.plan("case")
    assert planned["provider_calls"] == 0
    assert planned["request_profile"]["schema_profile"] == "none"
    assert planned["request_profile"]["request_timeout_seconds"] == 60
    assert planned["budget"] == {
        "call_number": 1,
        "call_limit": 4,
        "max_call_cost_usd": 0.005,
        "max_aggregate_cost_usd": 0.10,
    }
    with pytest.raises(PipelineError) as caught:
        probe.execute("case", "0" * 64, client=client)
    assert caught.value.code == "plan_changed" and calls == []

    result = probe.execute("case", planned["plan_fingerprint"], client=client)
    assert result["state"] == "succeeded"
    assert len(calls) == 1
    assert calls[0]["extra_headers"] == {"X-Client-Request-Id": planned["diagnostic_id"]}
    trace = result["trace"]
    assert trace["provider_request_id"] == "req-minimal"
    assert trace["provider_response_id"] == "resp-minimal"
    assert trace["actual_cost_usd"] <= 0.005
    evidence = (tmp_path / "case/result.json").read_text(encoding="utf-8")
    assert "A solid color image." not in evidence
    assert "data:image" not in evidence
    assert json.loads(evidence)["trace"]["attempt"] == 1
    with pytest.raises(PipelineError) as caught:
        probe.execute("case", planned["plan_fingerprint"], client=client)
    assert caught.value.code == "call_limit" and len(calls) == 1


def test_streaming_production_probe_requires_its_own_exact_one_shot_approval(tmp_path, monkeypatch):
    monkeypatch.setattr(probe, "VALIDATION_PARENT", tmp_path.resolve())
    calls = []
    material = {
        "schema_version": 1,
        "kind": "full_production_request_low_reasoning_streaming",
        "diagnostic_id": "t030-vlm-call-3-streaming-test",
        "model": "gpt-5.6-luna",
        "endpoint_fingerprint": "e" * 64,
        "request_profile": {"response_transport": "streaming-response-v1"},
        "request_sha256": "a" * 64,
        "request_size_bytes": 123,
        "schema_sha256": "b" * 64,
        "source": {"access": "artifacts_read_only_no_resubmit"},
        "budget": probe.STREAMING_PRODUCTION_BUDGET,
    }
    prepared = {"request": {"stream": True}}

    class Analyzer:
        client = None

        @staticmethod
        def _transport_timeout():
            return 300

        def analyze(self, store, view, ocr, **kwargs):
            calls.append(kwargs)
            assert kwargs["prepared"]["request"]["stream"] is True
            return {
                "state": "succeeded",
                "error_code": None,
                "validation_failure_stage": None,
                "validation_failure_reason": None,
                "actual": {"cost_usd": 0.01},
                "submission_trace": {"attempt": 1},
            }

    def context(*, response_transport=None):
        assert response_transport == probe.STREAMING_RESPONSE_TRANSPORT
        return material, Analyzer(), object(), object(), {}, "", prepared

    monkeypatch.setattr(probe, "_production_context", context)
    client = SimpleNamespace()
    planned = probe.plan_production_streaming("streaming")
    assert planned["provider_calls"] == 0
    assert planned["budget"]["call_number"] == 3
    with pytest.raises(PipelineError) as caught:
        probe.execute_production_streaming("streaming", "0" * 64, client=client)
    assert caught.value.code == "plan_changed" and calls == []

    result = probe.execute_production_streaming(
        "streaming", planned["plan_fingerprint"], client=client
    )
    assert result["state"] == "succeeded"
    assert len(calls) == 1
    assert calls[0]["operation_id"] == material["diagnostic_id"]
    with pytest.raises(PipelineError) as caught:
        probe.execute_production_streaming(
            "streaming", planned["plan_fingerprint"], client=client
        )
    assert caught.value.code == "call_limit" and len(calls) == 1
