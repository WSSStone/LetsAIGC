# Quickstart: Plan and Approve

Run from the repository root after the core environment, `OPENAI_API_KEY`, local
ComfyUI, and SDXL model have been prepared as described in the
[Agent guide](../../docs/agent-quickstart.md). Local media generation still uses
Responses for planning and critique.

```powershell
mamba run -n letsaigc-core letsaigc --json agent plan `
  "Create a square stone inventory icon" --backend comfy --budget configs\agent\budget-local.yaml
```

Review the returned plan and budget. Copy the top-level `id` into `TASK_ID` and
`plan_fingerprint` into `FINGERPRINT`; then explicitly execute that exact plan:

```powershell
mamba run -n letsaigc-core letsaigc --json agent execute TASK_ID --approve FINGERPRINT
mamba run -n letsaigc-core letsaigc --json agent inspect TASK_ID
```

Planning stages inputs and records local state but performs no media generation.
It calls Responses and may incur API cost, reserving `$0.03 + $0.01 per input image`
within both the per-iteration and total USD budgets. Media generation requires the
exact fingerprint; JSON `execute` runs the task, while JSON `run` only plans.

The supplied budget files select 3 revisions after the initial generation. The type
default is 10 if `max_revisions` is omitted, and 10 is also the maximum allowed.
To continue a session, use the returned `session_id` with `agent plan --session`
or interactive `agent chat --session`; every new media task requires approval.
