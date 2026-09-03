# Data Model: Agent Critique and Media Orchestration

- **AgentEvaluation**: hard checks, score `0..1`, issues, revision suggestion, stop reason.
- **AgentIteration**: revision parameters, call, usage, candidate, evaluation.
- **AgentTaskRecord**: ordered iterations, best/final candidate, ledger, terminal reason.
- **AgentRunEvidence**: session/task/iteration ids, approval, provider/recipe, budget,
  critic, lineage, source/output hashes for RunManifest 1.2.

