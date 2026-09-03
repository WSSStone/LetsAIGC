# Feature Specification: Constrained Comfy Workflow Compiler

**Feature ID**: `011-constrained-comfy-compiler`  
**Created**: 2026-09-02  
**Status**: Approved

## User Scenarios & Testing

### User Story 1 - Compile a Safe Image Recipe (Priority: P1)

As an asset creator, I can generate or edit an image locally from an approved recipe
without the Agent constructing arbitrary ComfyUI JSON.

**Independent Test**: Text-to-image and SDXL image-to-image recipes compile to native
nodes, bounded inputs, declared outputs, and deterministic hashes.

### User Story 2 - Compile a Safe Video Recipe (Priority: P2)

As an asset creator, I can use stable Wan2.1 T2V or opt into experimental Wan2.2 I2V
when the catalog and local node inventory support it.

**Independent Test**: T2V compiles and validates; I2V is reported experimental and
does not block stable acceptance.

### User Story 3 - Upload a Verified Input (Priority: P3)

As an asset creator, my already-resolved local image is uploaded to a task-specific
ComfyUI subdirectory and referenced by the compiled graph.

**Independent Test**: Fake upload and object inventory produce a valid graph; a
missing node, bad port, custom node, or parameter overflow fails before `/prompt`.

### Edge Cases

- ComfyUI version lacks a recipe node or input type.
- Model is missing, unqualified, or hash-invalid.
- The Agent requests an undeclared tunable or output path.
- Runtime output differs from the recipe declaration.

## Requirements

- **FR-001**: Agent output MUST be a `GenerationPlan`, never an arbitrary graph.
- **FR-002**: The compiler MUST use committed, versioned recipes and native-node allowlists.
- **FR-003**: Recipes MUST bound all tunables, models, resources, outputs, and stability level.
- **FR-004**: v1 MUST support SD1.5/SDXL T2I, SDXL I2I, Wan2.1 T2V, and experimental Wan2.2 I2V.
- **FR-005**: Every graph MUST be validated against `/object_info` before `/prompt`.
- **FR-006**: Input images MUST use `/upload/image` and a task-specific subdirectory.
- **FR-007**: Compiled graph, derived contract, and hashes MUST stay under `.local/agent`.
- **FR-008**: The compiler MUST reject custom nodes, undeclared fields, type mismatches,
  parameter overflows, unsafe paths, missing models, and undeclared outputs.
- **FR-009**: Production export MUST still require a committed recipe and existing gates.

### Policy and Evidence Requirements

- **PER-001**: Recipe models inherit catalog license lanes.
- **PER-002**: Recipe GPU/time/output budgets are part of approval.
- **PER-003**: Recipe version, graph hash, object inventory evidence, and outputs are recorded.
- **PER-004**: Dynamic graphs and uploaded inputs remain local and task-scoped.
- **PER-005**: Allowlist and object-validation failures require contract tests.

### Key Entities

- **WorkflowRecipe**: Versioned allowed operations, nodes, tunables, models, outputs, and budgets.
- **CompiledWorkflow**: Task-local graph, derived contract, model set, output declarations, and hash.

## Success Criteria

- **SC-001**: Every supported recipe compiles deterministically for identical approved inputs.
- **SC-002**: 100% of custom, missing, mistyped, or out-of-range nodes/inputs fail before queueing.
- **SC-003**: Existing image/video workflow runners remain compatible.
- **SC-004**: Experimental I2V failure never blocks stable recipe acceptance.

## Assumptions

- No third-party custom nodes are installed in v1.
- Model synchronization remains an explicit expert/user action.
