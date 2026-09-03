# Feature Specification: Agent-first Architecture

**Feature ID**: `008-agent-first-architecture`  
**Created**: 2026-09-02  
**Status**: Approved  
**Input**: Reframe LetsAIGC as an Agent-driven game-asset workbench and separate
the Development Harness from runtime product capabilities.

## User Scenarios & Testing

### User Story 1 - Understand the Product Boundary (Priority: P1)

As an owner, I can identify which repository elements develop the product and
which elements are the product, without mistaking Speckit or Codex rules for a
runtime feature.

**Independent Test**: README, CLI help, package metadata, constitution, and the
architecture document use one consistent product definition.

**Acceptance Scenarios**:

1. **Given** a new contributor, **When** they read the entry documentation,
   **Then** they can distinguish the Development Harness from the product control plane.
2. **Given** an existing automation user, **When** they upgrade, **Then** all expert
   CLI commands remain available with their existing names.

### User Story 2 - See the Agent-first Layers (Priority: P2)

As an engineer, I can see how the CLI, Agent, deterministic tools, backends, and
run evidence fit together before adding a new capability.

**Independent Test**: The documented dependency direction contains no path from
runtime code into `.agents/` or `.specify/`.

**Acceptance Scenarios**:

1. **Given** the architecture description, **When** a new backend is proposed,
   **Then** its layer and allowed dependencies are unambiguous.

### Edge Cases

- Historical specs use “harness” as a mixed term.
- Existing scripts and tests rely on expert CLI commands.
- Local-only documentation remains ignored by Git.

## Requirements

### Functional Requirements

- **FR-001**: Committed product documentation MUST define LetsAIGC as an
  Agent-driven game-asset generation workbench.
- **FR-002**: `.agents/`, `.specify/`, `AGENTS.md`, and `specs/` MUST be identified
  as the Development Harness and MUST NOT be runtime dependencies.
- **FR-003**: `src/letsaigc`, CLI, configuration, schemas, backends, ComfyUI, and
  media pipelines MUST be identified as the product control plane.
- **FR-004**: Existing workflow, video, sprites, drama, train, model, tracking,
  export, and runpack commands MUST remain compatible.
- **FR-005**: Historical feature 001 MUST retain its content and gain an explicit
  compatibility note instead of rewriting history.

### Policy and Evidence Requirements

- **PER-001**: The constitution MUST separate Development Harness rules from Product Invariants.
- **PER-002**: Agent-first additions MUST preserve current local hardware and service boundaries.
- **PER-003**: Regression evidence MUST cover all existing command groups.
- **PER-004**: Runtime modules MUST NOT read or write Development Harness artifacts.
- **PER-005**: Agent authority, approval, input, and credential rules MUST be product invariants.

### Key Entities

- **Development Harness**: Codex and Speckit governance artifacts used only to develop the repository.
- **Product Control Plane**: Runtime code and contracts used to plan, execute, and record asset jobs.
- **Expert Interface**: Existing deterministic CLI commands reusable beneath the Agent.

## Success Criteria

### Measurable Outcomes

- **SC-001**: All committed entry points use the new product definition and zero
  runtime modules depend on Development Harness paths.
- **SC-002**: One architecture map accounts for every current and planned runtime domain.
- **SC-003**: Existing automated tests and command help continue to pass unchanged.

## Assumptions

- The historical name `001-project-harness` remains for traceability.
- This feature changes terminology and boundaries, not expert command behavior.
