# Agent CLI Contract

- `agent plan INTENT [--image SOURCE]... [--backend auto|comfy|openai] --budget FILE [--session SESSION_ID]`
- `agent execute TASK_ID --approve FINGERPRINT`
- `agent run INTENT [--image SOURCE]... [--backend auto|comfy|openai] --budget FILE`
- `agent chat [--session SESSION_ID] [--budget FILE]`
- `agent inspect SESSION_OR_TASK_ID`

The backend defaults to `auto`. `chat` defaults to `configs/agent/budget-local.yaml`;
`plan` and `run` require an explicit budget file. Session continuation is available
on `plan` and `chat`; `run` has no `--session` option.

Root `--json` precedes `agent`. Successful `plan` and JSON `run` return
`awaiting_approval` without generating media. Their top-level `id`, `session_id`, and
`plan_fingerprint` identify the task, session, and exact plan to review. Human-mode
`run` displays the plan and asks before executing; `execute --approve` executes the
approved task in either human or JSON mode. `chat` rejects JSON mode. `inspect` reads
local state without contacting the provider.

Planning may incur Responses API cost: it reserves `$0.03 + $0.01 per input image`
within the supplied per-iteration and total USD budgets. Input staging and local
state persistence are controlled orchestrator operations, not arbitrary-write tools.
Media generation requires the separate exact fingerprint approval.
