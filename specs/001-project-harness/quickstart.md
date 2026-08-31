# Quickstart

Run from the repository root; bootstrap never changes global PowerShell policy.

```powershell
.\scripts\bootstrap.ps1 -Component core
mamba run -n letsaigc-core letsaigc doctor
mamba run -n letsaigc-core letsaigc models list
.\scripts\bootstrap.ps1 -Component comfy
mamba run -n letsaigc-core letsaigc models sync smoke-sd15
mamba run -n letsaigc-core letsaigc comfy serve
```

From a second terminal:

```powershell
mamba run -n letsaigc-core letsaigc workflow run sd15-smoke --set prompt="pixel art potion"
mamba run -n letsaigc-core letsaigc eval run 2d-baseline
.\scripts\bootstrap.ps1 -Component train
mamba run -n letsaigc-core letsaigc train sdxl-lora --config configs/training/sdxl-lora-smoke.yaml
```

Every command accepts `--json`. Runs live under `.local/runs`; MLflow and DVC are
local. Production export requires a succeeded, verified and human-approved run.
