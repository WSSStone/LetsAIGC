# Provider Contract

The remote adapter accepts only an approved `GenerationPlan` and verified
`ResolvedAsset` values. It returns bytes, request id, pinned snapshot, usage,
pricing version, actual cost, and a redacted request hash. It never returns or logs
credentials or raw URL query/fragment data.

