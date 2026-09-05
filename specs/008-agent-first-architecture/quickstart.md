# Quickstart: Verify the Boundary

```powershell
conda run --no-capture-output -n letsaigc-core letsaigc --help
conda run --no-capture-output -n letsaigc-core pytest
```

Confirm README names the product a workbench, all expert groups remain visible,
and runtime code has no dependency on `.agents`, `.specify`, or `specs`.

