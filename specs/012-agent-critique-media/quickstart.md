# Quickstart: Offline Agent Loop

```powershell
mamba run -n letsaigc-core pytest tests\integration\test_agent_flow.py
mamba run -n letsaigc-core pytest tests\integration\test_agent_guardrails.py tests\contract\test_openai_sdk_transport.py
```

These use fake models/backends and the real OpenAI SDK with an offline HTTP transport. Live paid/GPU smoke
tests remain explicit opt-in acceptance steps.
