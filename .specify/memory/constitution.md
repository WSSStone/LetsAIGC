<!--
Sync Impact Report
- Version change: 1.0.0 -> 2.0.0
- Modified principles:
  - I. Reproducible Runs -> Product Invariant I. Reproducible Runs
  - II. License-Segregated Promotion -> Product Invariant II. License-Segregated Promotion
  - III. Local-First Resource Budgets -> Product Invariant III. Local-First Resource and Cost Budgets
  - IV. Workflow as Code -> Product Invariant IV. Constrained Workflows as Code
  - V. Provenance and Quality Gates -> Product Invariant V. Provenance and Quality Gates
- Added sections: Product Definition and Control Planes; Development Harness
- Removed sections: none (Development Workflow was replaced by the more explicit Development Harness)
- Templates updated:
  - ✅ .specify/templates/plan-template.md
  - ✅ .specify/templates/spec-template.md
  - ✅ .specify/templates/tasks-template.md
- Runtime guidance updated:
  - ✅ README.md
  - ✅ AGENTS.md manual guidance
- Follow-up TODOs: none
-->

# LetsAIGC Constitution

## Product Definition and Control Planes

LetsAIGC is an agent-driven game-asset generation workbench. A multimodal LLM
Agent interprets user intent, drafts a bounded generation plan, obtains explicit
approval, invokes deterministic local or remote generation tools, evaluates the
result, and records provenance. The product control plane consists of
`src/letsaigc`, its CLI, configurations, schemas, ComfyUI integration, generation
backends, and media pipelines.

The Development Harness consists only of `.agents/`, `.specify/`, `AGENTS.md`,
Speckit artifacts, and the Codex Plan Mode workflow. It constrains how Codex
develops this repository. It MUST NOT be presented as a product capability,
runtime service, or user-facing subsystem.

## Product Invariants

### I. Reproducible Runs

Every media generation, evaluation, training, and post-processing run MUST record
the repository revision, external runtime and provider revisions, model identifiers
and hashes or snapshots, workflow/configuration hashes, input hashes, seeds,
effective parameters, relevant hardware/software versions, approval fingerprint,
budget usage, and output hashes. A result that cannot be reconstructed from its
manifest MUST NOT be promoted or represented as production-ready.

### II. License-Segregated Promotion

Every local model, remote provider model, and adapter MUST have an explicit license
or terms identifier and one of the `production`, `conditional`, or `restricted`
lanes. Unknown terms are denied. Conditional models require a local, explicit
attestation before use. Restricted models MAY be used for isolated research, but
their runs and derivatives MUST be blocked from production export. License or terms
acceptance MUST never be automated.

### III. Local-First Resource and Cost Budgets

Backend selection MUST prefer a qualified local capability when it satisfies the
approved intent. Every plan MUST declare its disk, VRAM, RAM, network, runtime,
remote cost, per-iteration, and total budgets before generation. The baseline MUST
remain usable on Windows 11, one RTX 3080 with 10 GiB VRAM, and 64 GiB RAM. Core
services MUST bind to loopback only. Pre-approval Agent planning MAY consume only
the explicit planning reserve in the user-supplied budget. No paid media-generation
request or GPU generation MAY occur before an immutable execution envelope has been approved.

### IV. Constrained Workflows as Code

Canonical workflows, recipes, model/provider catalogs, pricing tables, training
configurations, schemas, and runtime locks MUST be version-controlled text. An
Agent MUST NOT emit or execute arbitrary ComfyUI graphs: it may select only a
versioned recipe whose compiler validates native nodes, parameters, models,
resources, and outputs. Dynamically compiled graphs remain local and production
export requires a committed recipe and verified graph hash. Public CLI and manifest
contracts MUST be schema-validated, documented, and covered by contract tests.

### V. Provenance and Quality Gates

Git MUST contain source, specifications, configuration, schemas, tests, and pointers,
not secrets, third-party base weights, raw private datasets, local conversations, or
transient outputs. DVC tracks owned datasets and promoted adapters; MLflow tracks
experiment and run evidence. Promotion requires policy checks plus explicit human
approval. The Agent MUST NOT download models, train adapters, provide human approval,
or perform production export. Failed checks and unsuccessful revisions MUST remain
visible and MUST NOT be silently bypassed.

## Operational Constraints

- Agent tools are allowlisted and sequential. The Agent has no shell, arbitrary
  filesystem write, model download, training, approval, or export capability.
- Approval fixes the backend, model, recipe, input hashes, output count, maximum
  dimensions/quality/steps, and all budgets. A material change invalidates approval.
- Automatic revisions may adjust only prompts, seeds, and recipe-declared tunables;
  they stop at the approved revision or budget limit.
- Remote credentials MUST come from process environment or an ignored local `.env`.
  Logs, manifests, errors, and runpacks MUST never contain credential values or URL
  query/fragment secrets.
- Input URLs and media metadata are untrusted. Remote assets MUST pass SSRF, size,
  MIME, magic-byte, decode, and redirect checks before any Agent or backend sees them.
- Third-party weights MUST use safe serialization, pinned revisions, and SHA-256
  verification. Pickle-based checkpoints and unknown licenses are denied by default.
- Custom ComfyUI nodes are denied in the baseline. Services MUST NOT expose
  unauthenticated endpoints beyond `127.0.0.1`.
- Tools MUST NOT terminate unrelated GPU processes or change machine-wide shell
  execution policy. Resource pressure is reported to the operator.

## Development Harness

1. Enter Codex Plan Mode (`codex.plan`) for read-only discovery and a
   decision-complete implementation plan.
2. Use Speckit Specify, Clarify, Plan, Tasks, and Analyze in that order for product
   features. Development Harness maintenance itself does not consume a product
   feature number.
3. Re-check this constitution before implementation and after design.
4. Write contract, policy, provenance, security, and failure-path tests before the
   corresponding implementation.
5. Implement only approved tasks, run automated verification, and record any
   hardware or paid-provider checks separately from offline acceptance.
6. New product constraints return the work to `codex.plan`; the Development Harness
   remains a development control plane, never a runtime feature.

## Governance

This constitution overrides conflicting feature plans and implementation notes.
Amendments require a written rationale, a migration or compatibility note, and
updates to dependent templates in the same change. Versions follow semantic
versioning: MAJOR for incompatible principle changes, MINOR for new or materially
expanded governance, and PATCH for clarifications. Every Speckit plan and review
MUST report both Development Harness compliance and Product Invariant compliance;
exceptions require explicit documentation and MUST NOT weaken license, provenance,
budget, approval, credential, or input-safety gates.

**Version**: 2.0.0 | **Ratified**: 2026-08-31 | **Last Amended**: 2026-09-02
