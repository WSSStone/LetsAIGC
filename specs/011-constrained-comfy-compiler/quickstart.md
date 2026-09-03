# Quickstart: Compile Without Queueing

```powershell
mamba run -n letsaigc-core letsaigc --json agent plan `
  "Create an SDXL variation" --image C:\assets\input.png `
  --backend comfy --budget configs\agent\budget-local.yaml
```

After approval, inspect the task-local compiled graph and its recipe/graph hash.
Missing models are diagnosed with an explicit `models sync` suggestion, never downloaded.

