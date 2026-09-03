# Quickstart: Safe Remote Image Plan

```powershell
$env:OPENAI_API_KEY = "set-outside-history"
mamba run -n letsaigc-core letsaigc --json doctor
mamba run -n letsaigc-core letsaigc --json agent plan `
  "Restyle this as a watercolor item icon" --image C:\assets\potion.png `
  --backend openai --budget configs\agent\budget-remote-low.yaml
```

Planning verifies and copies the input but does not make a paid request.

