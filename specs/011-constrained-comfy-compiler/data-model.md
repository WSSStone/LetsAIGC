# Data Model: Constrained Comfy Workflow Compiler

- **WorkflowRecipe**: id/version, intent, stability, graph template, native allowlist,
  models, parameter bounds, outputs, resource budget, mutable revision fields.
- **CompiledWorkflow**: recipe identity, concrete graph/contract, input/model/output
  evidence, graph hash, task-local paths, validation status.

