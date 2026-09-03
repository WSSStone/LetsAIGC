# Feature Specification: LetsAIGC Project Harness

> **Historical compatibility note (2026-09-02):** This specification predates the
> Agent-first product definition and mixes Development Harness governance with
> foundational product infrastructure. Its name and history are retained for
> traceability only. New product work MUST NOT extend “harness” as a runtime concept;
> see features 008–012 and Constitution 2.0.0.

**Feature ID**: `001-project-harness`  
**Created**: 2026-08-31  
**Status**: Approved  
**Input**: Initialize a local-first, reproducible 2D game-asset AIGC engineering
workspace with ComfyUI, governed model usage, experiment tracking, and an SDXL
LoRA training baseline.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Start a Governed Project (Priority: P1)

As the project owner, I can open the repository and immediately understand which
files are source-controlled, which files are local-only, and which planning and
quality gates apply before implementation or promotion.

**Why this priority**: Every later model, workflow, and training run depends on a
safe repository boundary and a shared operating method.

**Independent Test**: A clean repository inspection shows the documented tree,
ignored local paths, completed governance rules, and an executable feature task list.

**Acceptance Scenarios**:

1. **Given** the initialized repository, **When** a local model, secret, output, or
   `.doc` file is created, **Then** Git ignores it while specifications and manifests
   remain visible.
2. **Given** a new feature request, **When** the owner follows the documented
   workflow, **Then** planning, Speckit artifacts, tests, and promotion evidence are
   produced in the required order.

---

### User Story 2 - Run Local 2D Generation (Priority: P2)

As an asset creator, I can bootstrap and start a local ComfyUI runtime, verify the
machine and model inventory, and run approved 2D generation workflows without
exposing the service to other machines.

**Why this priority**: Local inference is the first useful production capability
and establishes the runtime contract used by automation.

**Independent Test**: From a prepared machine, the owner can start the runtime,
query its health, and produce fixed-seed images from the baseline model profiles.

**Acceptance Scenarios**:

1. **Given** a compatible Windows/NVIDIA workstation, **When** bootstrap completes,
   **Then** the runtime uses an isolated environment and a recorded source revision.
2. **Given** an approved model profile, **When** its models are synchronized,
   **Then** every file has an allowed format, known license lane, pinned revision,
   and verified hash.
3. **Given** a running local service, **When** its listening address is inspected,
   **Then** it is bound only to the loopback interface.

---

### User Story 3 - Automate and Evaluate Workflows (Priority: P3)

As an engineer, I can submit validated workflow inputs, wait for completion, record
the complete run provenance, and compare a repeatable 2D asset evaluation suite.

**Why this priority**: A visual-only workflow cannot provide repeatable production
automation or defensible evaluation evidence.

**Independent Test**: A fixed workflow and parameter set produces a completed run
manifest, tracked metrics, and output hashes without manual graph editing.

**Acceptance Scenarios**:

1. **Given** a workflow contract, **When** invalid or incomplete input is submitted,
   **Then** execution is rejected before it reaches the generation queue.
2. **Given** valid input, **When** execution finishes, **Then** the manifest contains
   source, model, workflow, parameter, environment, output, and timing evidence.
3. **Given** a production export request, **When** license or human-review gates are
   incomplete, **Then** export is blocked with a specific reason.

---

### User Story 4 - Train and Reuse an SDXL LoRA (Priority: P4)

As a model developer, I can run a memory-bounded SDXL LoRA smoke training job,
track its dataset and experiment, and reload the resulting adapter for inference.

**Why this priority**: Local adaptation is a core project goal but depends on the
runtime, provenance, and policy foundations delivered by earlier stories.

**Independent Test**: A fixed small dataset completes a 20-step training smoke run,
produces a safe adapter, records its lineage, and successfully loads for inference.

**Acceptance Scenarios**:

1. **Given** insufficient currently free GPU memory, **When** training is requested,
   **Then** the owner receives a warning or explicit low-memory profile selection;
   unrelated processes are not terminated.
2. **Given** the smoke profile, **When** training completes, **Then** the adapter is
   linked to the base model, dataset revision, effective configuration, and metrics.
3. **Given** a verified adapter, **When** it is loaded into an approved workflow,
   **Then** inference completes and records the adapter hash in the run manifest.

### Edge Cases

- The machine has the supported GPU model but less free VRAM than the profile budget.
- A gated model is requested without an explicit local license acceptance record.
- A model hash differs from the catalog after download or cache reuse.
- A workflow requires a missing or unapproved custom node.
- ComfyUI exits during a queued job or returns node validation errors.
- The DVC remote, experiment store, or target export path is unavailable.
- The repository has no commit yet, so feature setup must not invent a commit hash.
- Identical seeds are run under different dependency or model revisions.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The repository MUST distinguish committed engineering artifacts from
  local thinking, runtimes, models, datasets, outputs, caches, and secrets.
- **FR-002**: The repository MUST document the Codex Plan Mode and Speckit lifecycle,
  including the point at which implementation and promotion are allowed.
- **FR-003**: The system MUST provide isolated core, ComfyUI, and SDXL training
  environments with recorded dependency locks.
- **FR-004**: The system MUST diagnose GPU, VRAM, memory, disk, tools, ports, runtime,
  and model readiness without changing unrelated machine state.
- **FR-005**: The system MUST start ComfyUI on loopback only and verify its health,
  node inventory, queue, history, and execution progress interfaces.
- **FR-006**: The system MUST maintain a catalog for SD1.5, SDXL 1.0 Base,
  FLUX.1 Schnell FP8, SD3.5 Medium, and FLUX.1 Dev.
- **FR-007**: Model synchronization MUST enforce source revision, safe format,
  SHA-256, access requirements, license lane, and target location.
- **FR-008**: The system MUST represent every automated workflow with editable and
  API forms plus a validated input/output/resource contract.
- **FR-009**: The system MUST submit valid workflows, observe completion, retrieve
  results, and produce a complete run manifest.
- **FR-010**: The system MUST track experiment parameters, metrics, and artifacts,
  and version owned datasets and promoted adapters.
- **FR-011**: The system MUST provide fixed baseline evaluations for character,
  prop/icon, material/texture, and style-consistency 2D asset scenarios.
- **FR-012**: The system MUST provide an SDXL LoRA smoke profile and a low-memory
  profile that never silently changes effective training parameters.
- **FR-013**: The system MUST load a verified trained adapter in a generation run.
- **FR-014**: Production export MUST require an allowed license lane, verified
  hashes, successful validation, complete provenance, and human approval.
- **FR-015**: The system MUST build a provider-neutral runpack without embedding
  third-party base weights or secrets.
- **FR-016**: The system MUST preserve external user files and MUST NOT create Git
  commits, accept licenses, expose services, or terminate processes on the user's
  behalf without explicit authorization.

### Policy and Evidence Requirements

- **PER-001**: Every model and adapter MUST declare a production, conditional, or
  restricted license lane and explicit export behavior.
- **PER-002**: The supported baseline is Windows 11, RTX 3080 10 GiB, 64 GiB RAM,
  with per-profile VRAM, RAM, disk, network, and runtime budgets.
- **PER-003**: Acceptance evidence MUST include revisions, hashes, effective
  parameters, environment details, output hashes, automated tests, smoke results,
  and human review where promotion is requested.
- **PER-004**: Third-party base models and transient outputs remain local; owned
  datasets and promoted adapters use DVC; final game assets are exported externally.

### Key Entities

- **Model Entry**: Source, revision, files, hashes, license lane, access gate,
  runtime profiles, and resource guidance for one model family.
- **Workflow Contract**: Editable/API representations, parameter schema, required
  models/nodes, output declaration, resource budget, and export policy.
- **Run Manifest**: Immutable provenance for one inference, evaluation, or training run.
- **Adapter Record**: Base model, owned dataset revision, training configuration,
  output hash, evaluation status, and promotion state for a LoRA.
- **License Attestation**: Machine-local record of an explicit gated or conditional
  model acknowledgement; never a substitute for the actual license.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: All local-only paths and prohibited weight formats are ignored or
  rejected in automated checks, with zero secrets or base-model binaries staged.
- **SC-002**: A new owner can run one diagnostic command and receive actionable
  pass, warning, or failure results for every required local dependency.
- **SC-003**: The local generation service passes all health/queue/history checks
  and accepts connections only from the same machine.
- **SC-004**: Each installed model file is traceable to one catalog revision and
  verified hash; corrupted or unacknowledged files are rejected 100% of the time.
- **SC-005**: A fixed valid workflow completes with a manifest containing every
  required provenance field; invalid inputs are rejected before queue submission.
- **SC-006**: The four-scenario evaluation suite records completion, elapsed time,
  peak VRAM when available, output hashes, and review placeholders for every run.
- **SC-007**: The 20-step SDXL LoRA smoke run either completes and reloads its
  adapter or exits with a specific resource/dependency failure and no false success.
- **SC-008**: Production export rejects every restricted, unknown, unattested,
  unverified, failed, or unapproved run in automated tests.

## Assumptions

- v1 is single-user, local-only, and limited to 2D image assets.
- The owner closes discretionary GPU-heavy applications before measured smoke runs.
- Gated model authentication and license acceptance remain interactive owner actions.
- Cloud providers, 3D, video, and multi-user serving are separate future features.
- The repository manages pipelines and adapters, not final game-asset binary history.
