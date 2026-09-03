# Research: Agent-first Architecture

- **Decision**: Keep existing expert modules in place and add a one-way Agent layer.
  **Rationale**: Preserves stable contracts and avoids churn. **Alternative**: Move
  all runtime modules; rejected because it adds migration risk without user value.
- **Decision**: Constitution 2.0.0 separates Development Harness and Product Invariants.
  **Rationale**: The earlier term caused the exact boundary confusion this feature fixes.

