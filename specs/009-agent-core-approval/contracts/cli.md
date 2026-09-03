# Agent CLI Contract

- `agent plan INTENT [--image SOURCE]... [--backend auto|comfy|openai] --budget FILE`
- `agent execute TASK_ID --approve FINGERPRINT`
- `agent run INTENT ...`
- `agent chat [--session SESSION_ID] [--budget FILE]`
- `agent inspect SESSION_OR_TASK_ID`

Root `--json` is non-interactive and `run` returns `awaiting_approval`.

