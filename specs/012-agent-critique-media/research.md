# Research: Agent Critique and Media Orchestration

- **Decision**: Deterministic hard checks precede visual scoring. **Rationale**:
  subjective quality cannot override size, alpha, codec, or safety failures.
- **Decision**: Accept at score 0.8; retain best hard-valid candidate otherwise.
- **Decision**: Video uses fixed-interval contact sheets and ffprobe metadata.
  **Rationale**: The configured Agent model receives controlled image evidence.
- **Decision**: Existing Sprite/drama services are wrapped, not rewritten.

