# Quickstart: Plan, Execute, and Inspect a Compiled Workflow

From the repository root, prepare the core environment, `OPENAI_API_KEY`, locked
ComfyUI service, and SDXL model using the [Agent guide](../../docs/agent-quickstart.md).
Replace the example image with a file you may use.

```powershell
mamba run -n letsaigc-core letsaigc --json agent plan `
  "Create an SDXL variation" --image C:\assets\input.png `
  --backend comfy --budget configs\agent\budget-local.yaml
```

Planning calls Responses and may incur API cost. It reserves
`$0.03 + $0.01 per input image` within the supplied per-iteration and total USD caps.
It returns a plan without compiling or queueing a generation workflow.

Review the plan and budget. Replace `TASK_ID` and `FINGERPRINT` with the returned
top-level `id` and `plan_fingerprint`, then explicitly execute:

```powershell
mamba run -n letsaigc-core letsaigc --json agent execute TASK_ID --approve FINGERPRINT
mamba run -n letsaigc-core letsaigc --json agent inspect TASK_ID
```

Execution verifies models, uploads the validated input, compiles and validates the
workflow, then queues generation. The CLI has no separate compile-only command;
`execute` is not a dry run. Missing models are diagnosed with an explicit
`models sync` suggestion, never downloaded by the Agent.

In the inspected task, each completed candidate's `iterations[].candidate.run_id`
identifies its child run. Read `.local/runs/CHILD_RUN_ID/manifest.json`; its `source`
contains `compiled_graph_path`, `compiled_contract_path`, `compiled_graph_sha256`,
`compiled_contract_sha256`, `recipe_id`, and `recipe_version`. Compiled files live
under `.local/agent/sessions/SESSION_ID/tasks/TASK_ID/compiled/`. A failure before
compilation may have no compiled artifact or child candidate.
