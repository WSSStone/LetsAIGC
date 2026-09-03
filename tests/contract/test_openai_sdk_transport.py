from __future__ import annotations

import json

import httpx
from openai import OpenAI

from letsaigc.agent.responses import ResponsesAgentModel


def test_real_sdk_serializes_stateless_sequential_planning_and_critique() -> None:
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        requests.append(body)
        if body["text"]["format"]["name"] == "generation_intent":
            payload = {
                "intent": "text_to_image", "prompt": "potion", "negative_prompt": "text",
                "acceptance_criteria": ["centered"], "parameters": {},
            }
        else:
            payload = {"score": 0.9, "issues": [], "revision": {"prompt": None, "seed": None}}
        return httpx.Response(200, headers={"x-request-id": "req-offline"}, json={
            "id": "resp-offline", "object": "response", "created_at": 1, "status": "completed",
            "model": "gpt-5.6-luna", "parallel_tool_calls": False,
            "output": [{"id": "msg-offline", "type": "message", "role": "assistant", "status": "completed",
                        "content": [{"type": "output_text", "text": json.dumps(payload), "annotations": []}]}],
            "usage": {"input_tokens": 100, "output_tokens": 40, "total_tokens": 140},
        })

    with OpenAI(api_key="test-key-not-real", max_retries=0,
                http_client=httpx.Client(transport=httpx.MockTransport(handler))) as client:
        model = ResponsesAgentModel(client)
        assert model.interpret("potion", [], [])["intent"] == "text_to_image"
        assert model.evaluate("potion", [], {"dimensions": True}).accepted
    for body in requests:
        assert body["store"] is False
        assert body["parallel_tool_calls"] is False
        assert body["service_tier"] == "default"
        assert "test-key" not in json.dumps(body)
        _assert_strict_objects(body["text"]["format"]["schema"])


def _assert_strict_objects(schema):
    if isinstance(schema, dict):
        if schema.get("type") == "object":
            assert schema.get("additionalProperties") is False
            assert set(schema.get("required", [])) == set(schema["properties"])
        for value in schema.values():
            _assert_strict_objects(value)
    elif isinstance(schema, list):
        for value in schema:
            _assert_strict_objects(value)
