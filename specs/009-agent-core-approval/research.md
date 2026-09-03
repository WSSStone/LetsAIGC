# Research: Agent Core and Approval

- **Decision**: Direct Responses API with structured outputs and custom function tools;
  no Agent framework. **Rationale**: The state machine and authority boundary remain explicit.
- **Decision**: Canonical JSON SHA-256 fingerprint over the complete execution envelope.
  **Rationale**: Exact approval matching is portable and testable.
- **Decision**: Atomic local JSON after every transition with `store=false` remotely.
  **Rationale**: Recovery and privacy do not depend on provider conversation storage.

