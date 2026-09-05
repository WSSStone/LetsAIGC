# Quickstart: Safe Remote Image Plan

From the repository root, prepare the core environment and configure `OPENAI_API_KEY`
in the process environment or ignored `.env`; see the
[Agent guide](../../docs/agent-quickstart.md). The remote image path requires no local
ComfyUI service or weights. Replace the example image with a file you may use.

```powershell
conda run --no-capture-output -n letsaigc-core letsaigc --json doctor
conda run --no-capture-output -n letsaigc-core letsaigc --json agent plan `
  "Restyle this as a watercolor item icon" --image C:\assets\potion.png `
  --backend openai --budget configs\agent\budget-remote-low.yaml
```

`doctor` makes no paid request. Planning verifies and stages the input, persists
local state, and calls Responses. It may incur API cost and reserves
`$0.03 + $0.01 per input image` within the supplied per-iteration and total USD caps.
It does not generate media or authorize a paid image request.

Review the result before copying its top-level `id` and `plan_fingerprint` into
`agent execute TASK_ID --approve FINGERPRINT`. That command performs generation,
including in JSON mode. The supplied remote budget allows one initial generation
and up to 3 revisions; the task also stops when its approved budget is exhausted.
