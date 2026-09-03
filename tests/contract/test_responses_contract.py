from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from letsaigc.agent.responses import ResponsesAgentModel
from letsaigc.errors import RuntimeExecutionError


class FakeResponses:
    def __init__(self) -> None:
        self.kwargs = None

    def create(self, **kwargs):
        self.kwargs = kwargs
        return SimpleNamespace(
            usage={"input_tokens": 100, "output_tokens": 50},
            output_text=json.dumps(
                {
                    "intent": "text_to_image",
                    "prompt": "potion icon",
                    "negative_prompt": "text",
                    "acceptance_criteria": ["centered"],
                }
            )
        )


def test_responses_disables_storage_and_parallel_tools() -> None:
    responses = FakeResponses()
    model = ResponsesAgentModel(SimpleNamespace(responses=responses))
    result = model.interpret("make a potion", [], [])
    assert result["intent"] == "text_to_image"
    assert responses.kwargs["model"] == "gpt-5.6-luna"
    assert responses.kwargs["store"] is False
    assert responses.kwargs["parallel_tool_calls"] is False
    assert responses.kwargs["reasoning"] == {"effort": "medium"}


def test_planning_function_loop_is_sequential_and_bills_all_calls() -> None:
    class ToolResponses(FakeResponses):
        count = 0

        def create(self, **kwargs):
            self.count += 1
            if self.count == 1:
                return SimpleNamespace(output_text="", usage={"input_tokens": 100, "output_tokens": 20}, output=[
                    SimpleNamespace(type="function_call", name="inspect_capabilities", arguments="{}", call_id="c1"),
                ])
            assert kwargs["input"][-1]["type"] == "function_call_output"
            return super().create(**kwargs)

    responses = ToolResponses()
    model = ResponsesAgentModel(SimpleNamespace(responses=responses))
    model.interpret("potion", [], [])
    assert responses.count == 2
    assert model.last_usage["input_tokens"] == 200
    assert model.last_usage["output_tokens"] == 70


def test_provider_errors_redact_key(monkeypatch) -> None:
    class BrokenResponses:
        def create(self, **kwargs):
            raise ValueError("failure: unit-secret-key")

    monkeypatch.setenv("OPENAI_API_KEY", "unit-secret-key")
    with pytest.raises(RuntimeExecutionError) as caught:
        ResponsesAgentModel(SimpleNamespace(responses=BrokenResponses())).interpret("potion", [], [])
    assert "unit-secret-key" not in str(caught.value.details)
