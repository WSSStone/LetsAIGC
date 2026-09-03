# Feature Specification: Agent Core and Approval

**Feature ID**: `009-agent-core-approval`  
**Created**: 2026-09-02  
**Status**: Approved

## User Scenarios & Testing

### User Story 1 - Plan Before Spending (Priority: P1)

As an asset creator, I can describe an intent and receive a concrete generation
plan, immutable approval fingerprint, and worst-case budget without executing it.

**Independent Test**: Planning with fake capabilities creates an `awaiting_approval`
task and performs no generation tool call.

**Acceptance Scenarios**:

1. **Given** a valid intent and budget, **When** I request a plan, **Then** it fixes
   backend, model, recipe, inputs, output envelope, and cost/GPU limits.
2. **Given** JSON mode, **When** I run the combined command, **Then** it returns
   `awaiting_approval` without prompting or executing.

### User Story 2 - Execute Exactly What Was Approved (Priority: P2)

As an asset creator, I can execute a task only with the exact plan fingerprint and
see every state transition and budget entry.

**Independent Test**: A stale fingerprint and an expanded envelope are rejected;
the exact fingerprint reaches the allowlisted dispatcher.

**Acceptance Scenarios**:

1. **Given** an approved plan, **When** the fingerprint matches, **Then** only tools
   inside its envelope are available.
2. **Given** a changed backend, model, recipe, quality, size, or budget, **When**
   execution is requested, **Then** approval is invalidated.

### User Story 3 - Resume a Local Conversation (Priority: P3)

As an asset creator, I can resume a local Agent session while each media-producing
intent remains a separately approved task.

**Independent Test**: Restarting the process reconstructs session/task state from
`.local` without remote stored conversation state.

**Acceptance Scenarios**:

1. **Given** a prior local session, **When** I inspect or resume it, **Then** its
   turns, current task, and terminal state are available.

### Edge Cases

- Budget is missing, negative, exhausted, or changed after planning.
- Process stops between tool result and state persistence.
- Agent requests a prohibited or unordered tool.
- Revision limit is outside `0..10`.

## Requirements

### Functional Requirements

- **FR-001**: The Agent MUST use the configured Responses model with medium reasoning,
  structured outputs, sequential function tools, and remote storage disabled.
- **FR-002**: Planning MUST end at `awaiting_approval` and MUST NOT execute generation.
- **FR-003**: Approval MUST bind a canonical SHA-256 fingerprint to an immutable envelope.
- **FR-004**: Every task MUST enforce per-iteration and total USD and GPU-minute budgets.
- **FR-005**: Automatic revisions MUST be limited to `0..10`, defaulting to 10.
- **FR-006**: Pre-approval tools MUST be read-only; post-approval tools MUST be the
  subset declared in the approved envelope.
- **FR-007**: The Agent MUST have no shell, arbitrary-write, model-download,
  training, human-approval, or production-export tool.
- **FR-008**: Sessions and task records MUST persist locally and be recoverable.
- **FR-009**: CLI MUST expose `agent plan`, `execute`, `run`, `chat`, and `inspect`.
- **FR-010**: Human-readable `run` MUST ask for confirmation; JSON `run` MUST never interact.

### Policy and Evidence Requirements

- **PER-001**: Model license lanes remain part of the approved plan.
- **PER-002**: GPU and remote cost budgets are fail-closed before each tool call.
- **PER-003**: State transitions, tool calls, usage, fingerprints, and failures are evidence.
- **PER-004**: Sessions, turns, and dynamic plans remain in ignored `.local` storage.
- **PER-005**: Approval and tool-authority failures require contract tests.

### Key Entities

- **AgentSession**: Local turns, active task, and recovery state.
- **GenerationPlan**: Intent, backend, model, recipe, inputs, criteria, and envelope.
- **TaskBudget**: Total/per-iteration USD and GPU-minute caps plus revision limit.
- **ApprovalRecord**: Fingerprint, time, and immutable execution envelope.
- **AgentTaskRecord**: State, iterations, calls, budget ledger, candidates, and terminal reason.

## Success Criteria

### Measurable Outcomes

- **SC-001**: Zero generation or paid calls occur before exact fingerprint approval.
- **SC-002**: 100% of stale, expanded, over-budget, or prohibited calls fail closed.
- **SC-003**: A task survives process restart without losing its last completed state.
- **SC-004**: No execution performs more than one initial generation plus 10 revisions.

## Assumptions

- Single-user local CLI; no Web UI or public Agent HTTP API.
- Default Agent model is configurable but cannot switch itself.
