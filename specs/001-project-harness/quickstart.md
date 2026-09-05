# Quickstart

> **Historical expert entrypoint (2026-09-03):** These commands describe the original
> image/training workflow. For the current Agent entrypoint, setup, approval, and
> acceptance status, use the [Agent guide](../../docs/agent-quickstart.md). Expert
> commands remain available and execute directly; they do not use Agent approval.

Run from the repository root; bootstrap never changes global PowerShell policy.

```powershell
.\scripts\bootstrap.ps1 -Component core
conda run --no-capture-output -n letsaigc-core letsaigc doctor
conda run --no-capture-output -n letsaigc-core letsaigc models list
.\scripts\bootstrap.ps1 -Component comfy
conda run --no-capture-output -n letsaigc-core letsaigc models sync smoke-sd15
conda run --no-capture-output -n letsaigc-core letsaigc comfy serve
```

From a second terminal:

```powershell
conda run --no-capture-output -n letsaigc-core letsaigc workflow run sd15-smoke --set prompt="pixel art potion"
conda run --no-capture-output -n letsaigc-core letsaigc eval run 2d-baseline
.\scripts\bootstrap.ps1 -Component train
conda run --no-capture-output -n letsaigc-core letsaigc train sdxl-lora --config configs/training/sdxl-lora-smoke.yaml
```

For JSON-capable CLI commands, put the root option before the command, for example
`letsaigc --json doctor`; interactive `agent chat` does not support JSON mode.
Runs live under `.local/runs`; MLflow and DVC are local. Production export requires
a succeeded, verified and human-approved run.
