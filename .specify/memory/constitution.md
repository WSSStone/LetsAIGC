<!--
Sync Impact Report
- Version change: template -> 1.0.0
- Principles defined:
  - Placeholder Principle 1 -> I. Reproducible Runs
  - Placeholder Principle 2 -> II. License-Segregated Promotion
  - Placeholder Principle 3 -> III. Local-First Resource Budgets
  - Placeholder Principle 4 -> IV. Workflow as Code
  - Placeholder Principle 5 -> V. Provenance and Quality Gates
- Added sections: Operational Constraints; Development Workflow
- Removed sections: none
- Templates updated:
  - ✅ .specify/templates/plan-template.md
  - ✅ .specify/templates/spec-template.md
  - ✅ .specify/templates/tasks-template.md
- Follow-up TODOs: none
-->

# LetsAIGC Constitution

## Core Principles

### I. Reproducible Runs

Every inference, evaluation, and training run MUST record the repository revision,
external runtime revisions, model identifiers and hashes, workflow/configuration
hashes, seeds, effective parameters, relevant hardware/software versions, and
output hashes. A result that cannot be reconstructed from its manifest MUST NOT
be promoted or represented as production-ready.

### II. License-Segregated Promotion

Every model and adapter MUST have an explicit license identifier and one of the
`production`, `conditional`, or `restricted` lanes. Unknown licenses are denied.
Conditional models require a local, explicit attestation before use. Restricted
models MAY be used for isolated research, but their runs and derivatives MUST be
blocked from production export. License acceptance MUST never be automated.

### III. Local-First Resource Budgets

The v1 baseline MUST run on Windows 11, a single RTX 3080 with 10 GiB VRAM, and
64 GiB system RAM. Features MUST declare disk, VRAM, RAM, network, and expected
runtime budgets before implementation. Core services MUST bind to loopback only.
Cloud execution MAY consume a provider-neutral runpack, but local workflows MUST
remain usable without a cloud account.

### IV. Workflow as Code

Canonical workflows, model catalogs, training configurations, schemas, and
runtime locks MUST be version-controlled text. A ComfyUI workflow exposed by the
harness MUST have both an editable UI representation and a validated API form.
Public CLI and manifest contracts MUST be schema-validated, documented, and
covered by contract tests before implementation is considered complete.

### V. Provenance and Quality Gates

Git MUST contain source, specifications, configuration, manifests, and pointers,
not third-party base weights, raw private datasets, transient outputs, or secrets.
DVC MUST track owned datasets and promoted adapters; MLflow MUST track experiment
metadata and artifacts. Promotion requires automated checks plus explicit human
approval. Failed checks MUST remain visible and MUST NOT be silently bypassed.

## Operational Constraints

- Third-party weights MUST use safe serialization, pinned revisions, and SHA-256
  verification. Pickle-based checkpoints are denied by default.
- Custom ComfyUI nodes are denied in the baseline. Any later node MUST be pinned,
  reviewed, and recorded in the runtime lock before use.
- Services MUST NOT expose unauthenticated endpoints beyond `127.0.0.1`.
- Tools MUST NOT terminate unrelated GPU processes or change machine-wide shell
  execution policy. Resource pressure is reported to the operator.
- Final game assets are exported to a consuming project and are not retained as
  managed binary history in this repository.

## Development Workflow

1. Enter Codex Plan Mode (`codex.plan`) for read-only discovery and a
   decision-complete plan.
2. Use Speckit Specify, Clarify, Plan, Tasks, and Analyze in that order.
3. Re-check this constitution before implementation and after design.
4. Write contract and failure-path tests before the corresponding implementation.
5. Capture automated evidence, hardware smoke-test results, and human review before
   promotion. New constraints return the work to `codex.plan`.

## Governance

This constitution overrides conflicting feature plans and implementation notes.
Amendments require a written rationale, a migration or compatibility note, and
updates to dependent templates in the same change. Versions follow semantic
versioning: MAJOR for incompatible principle changes, MINOR for new or materially
expanded governance, and PATCH for clarifications. Every Speckit plan and review
MUST report constitution compliance; exceptions require explicit documentation
and MUST NOT weaken license, provenance, or safety gates.

**Version**: 1.0.0 | **Ratified**: 2026-08-31 | **Last Amended**: 2026-08-31
