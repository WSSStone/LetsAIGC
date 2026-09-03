# Quickstart: Plan and Approve

```powershell
mamba run -n letsaigc-core letsaigc --json agent plan `
  "Create a square stone inventory icon" --backend auto --budget configs\agent\budget-local.yaml
mamba run -n letsaigc-core letsaigc --json agent execute TASK_ID --approve FINGERPRINT
```

The first command performs no generation. The second rejects any fingerprint other
than the exact one returned by planning.

