# Data Model: Agent Core and Approval

- **AgentSession**: id, timestamps, turns, active task, status.
- **TaskBudget**: total/per-iteration USD and GPU-minute limits; revisions `0..10`.
- **GenerationPlan**: intent, backend, model, recipe, inputs, criteria, envelope.
- **ApprovalRecord**: plan fingerprint, approved time, immutable envelope.
- **AgentTaskRecord**: state machine, iterations, ledger, candidates, final selection/reason.

State flow: `intake -> resolve_assets -> interpret_intent -> inspect_capabilities ->
draft_plan -> validate_budget -> awaiting_approval -> execute -> normalize_output ->
evaluate -> accepted|revise -> finalize`.

