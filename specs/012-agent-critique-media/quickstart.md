# Quickstart: Offline Agent Loop

```powershell
conda run --no-capture-output -n letsaigc-core pytest tests\integration\test_agent_flow.py
conda run --no-capture-output -n letsaigc-core pytest tests\integration\test_agent_guardrails.py tests\contract\test_openai_sdk_transport.py
```

These use fake models/backends and the real OpenAI SDK with an offline HTTP transport. Live paid/GPU smoke
tests remain explicit opt-in acceptance steps.
