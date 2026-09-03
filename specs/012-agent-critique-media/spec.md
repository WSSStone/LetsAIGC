# Feature Specification: Agent Critique and Media Orchestration

**Feature ID**: `012-agent-critique-media`  
**Created**: 2026-09-02  
**Status**: Approved

## User Scenarios & Testing

### User Story 1 - Evaluate and Revise a Candidate (Priority: P1)

As an asset creator, I can have the multimodal Agent judge hard constraints and
visual fit, make bounded revisions, and stop with the best candidate and reason.

**Independent Test**: A fake critic fails twice and accepts the third candidate;
the task contains all candidates, scores, changes, usage, and terminal reason.

### User Story 2 - Critique Video Reliably (Priority: P2)

As an asset creator, video results are evaluated through metadata and a deterministic
contact sheet when the Agent model cannot consume video directly.

**Independent Test**: A fixture produces fixed interval frames, contact sheet,
ffprobe evidence, black-frame metrics, and reproducible hashes.

### User Story 3 - Orchestrate Existing Media Tools (Priority: P3)

As an asset creator, the Agent can invoke approved Sprite or short-drama processing
while preserving parent-child run lineage and existing promotion restrictions.

**Independent Test**: Fake generation followed by Sprite/drama dispatch produces
linked manifests; no approval, training, model sync, or export tool is exposed.

### Edge Cases

- Critic score is high while a hard constraint fails.
- Budget ends before the next revision or provider moderation refuses a result.
- Every revision is worse than an earlier candidate.
- Video is damaged or FFmpeg/ffprobe is missing.

## Requirements

- **FR-001**: Evaluation MUST combine deterministic hard constraints with a `0..1` critic score.
- **FR-002**: Acceptance requires all hard constraints and score `>=0.8`.
- **FR-003**: Revisions MUST change only prompt, negative prompt, seed, or recipe-declared tunables.
- **FR-004**: Backend/model/recipe/quality/size/budget changes MUST invalidate approval.
- **FR-005**: The loop MUST stop on acceptance, budget/revision exhaustion, user rejection,
  provider moderation, or unrecoverable error and retain the best candidate.
- **FR-006**: Video evaluation MUST use ffprobe metadata and a fixed-interval contact sheet.
- **FR-007**: Approved post-processing MAY call existing Sprite and drama services only.
- **FR-008**: RunManifest schema 1.2 MUST add Agent/remote kinds, identifiers,
  approval, budget, provider, critic, stop, and lineage fields while reading 1.0/1.1.
- **FR-009**: MLflow MUST use parent Agent runs and child generation/evaluation/postprocess runs.
- **FR-010**: No Agent path may download, train, approve, or production-export.

### Policy and Evidence Requirements

- **PER-001**: Final candidates inherit all model/provider license lanes.
- **PER-002**: Contact-sheet and post-processing time/disk are included in budgets.
- **PER-003**: Every iteration, metric, issue, suggestion, output, and stop reason is recorded.
- **PER-004**: Temporary frames/contact sheets remain local; promoted assets use existing DVC gates.
- **PER-005**: Critic/tool authority/revision/budget failure tests are mandatory.

### Key Entities

- **AgentEvaluation**: Hard checks, score, issues, suggestions, and stop reason.
- **AgentIteration**: Input revision, tool call, usage, result, and evaluation.
- **RunManifest 1.2 Agent Metadata**: Session/task/iteration, provider, approval, budget, critic, and stop evidence.

## Success Criteria

- **SC-001**: The loop never exceeds the approved revision or budget limits.
- **SC-002**: The highest-scoring hard-valid candidate is returned when acceptance is not reached.
- **SC-003**: Video contact-sheet output is deterministic for identical source and settings.
- **SC-004**: Agent, generation, evaluation, Sprite, and drama runs have complete parent-child lineage.
- **SC-005**: All existing low-level workflows and tests continue to pass.

## Assumptions

- Character/style continuity remains a human review concern.
- Live OpenAI/Comfy acceptance is opt-in and separate from offline automated tests.
