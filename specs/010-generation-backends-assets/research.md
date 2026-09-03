# Research: Generation Backends and Assets

- **Decision**: Manual redirect handling and address validation on every hop.
  **Rationale**: Automatic redirect following can bypass SSRF checks.
- **Decision**: Validate declared MIME, magic bytes, and decoded Pillow format.
  **Rationale**: No one signal is sufficient for untrusted media.
- **Decision**: GPT Image 2 uses the Image API and pinned snapshot; Responses built-in
  image tools are not exposed. **Rationale**: Local approval, budget, and provenance
  cannot be bypassed.
- **Decision**: Price entries have `effective_at` and `review_after` and fail stale.
  **Rationale**: Paid calls require current upper-bound reservations.

