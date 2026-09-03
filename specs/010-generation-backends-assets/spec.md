# Feature Specification: Generation Backends and Assets

**Feature ID**: `010-generation-backends-assets`  
**Created**: 2026-09-02  
**Status**: Approved

## User Scenarios & Testing

### User Story 1 - Supply Safe Reference Images (Priority: P1)

As an asset creator, I can attach one or more local images or HTTPS URLs and know
the Agent and backends use the same verified local bytes.

**Independent Test**: Valid PNG/JPEG/WebP inputs are copied and hashed; private
network, redirect, oversized, false-MIME, damaged, and active formats are rejected.

**Acceptance Scenarios**:

1. **Given** a valid local file, **When** it resolves, **Then** the source remains
   untouched and a session input copy records MIME, dimensions, and SHA-256.
2. **Given** a URL resolving to a private or reserved address at any redirect,
   **When** it resolves, **Then** it fails before content is accepted.

### User Story 2 - Route to a Qualified Backend (Priority: P2)

As an asset creator, I receive a local-first plan that uses remote image generation
only when local capability is insufficient or I explicitly request it.

**Independent Test**: A capability matrix deterministically selects local image/video,
rejects remote video, and respects explicit backend selection.

### User Story 3 - Generate or Edit with GPT Image 2 (Priority: P3)

As an asset creator, I can approve one pinned remote image request, settle actual
usage, and receive a hashed local result without exposing my key.

**Independent Test**: Fake Image API generation/edit responses produce a manifest
with request ID, snapshot, redacted request hash, usage, cost, and output hash.

### Edge Cases

- DNS rebinding or a redirect changes from public to private address.
- URL contains credentials, query secrets, or a misleading extension.
- Pricing is missing or past review date.
- Provider returns no image, malformed base64, moderation refusal, or partial usage.

## Requirements

### Functional Requirements

- **FR-001**: Only local regular files and HTTPS URLs may be input sources.
- **FR-002**: URL fetches MUST revalidate DNS after every redirect, permit at most
  3 redirects, 30 seconds, and 25 MiB, and reject non-public IP classes.
- **FR-003**: Accepted files MUST be PNG, JPEG, or WebP by MIME, magic, and decode.
- **FR-004**: Every source MUST be copied to the session, hashed, dimensioned, and
  represented by a query/fragment-free provenance value.
- **FR-005**: Automatic routing MUST be local-first; remote video MUST be unsupported.
- **FR-006**: The remote image backend MUST pin `gpt-image-2-2026-04-21`, generate
  from text, edit verified local bytes, and produce one output per iteration.
- **FR-007**: Remote sizes MUST be 1024x1024, 1024x1536, or 1536x1024; quality MUST
  be low, medium, or high; background MUST be auto, opaque, or transparent.
- **FR-008**: Pricing MUST be versioned, dated, source-linked, and fail closed when stale.
- **FR-009**: Worst-case cost MUST be reserved before a request and actual usage settled after it.
- **FR-010**: Credentials MUST come from process environment first, then ignored `.env`,
  and MUST never appear in logs, manifests, exceptions, or serialized requests.

### Policy and Evidence Requirements

- **PER-001**: Provider entries declare snapshot, terms, production lane, and credential name.
- **PER-002**: Cost budgets are enforced before and after every paid request.
- **PER-003**: Inputs, request hash, request ID, usage, pricing version, cost, and outputs are recorded.
- **PER-004**: Original files remain untouched; session copies and remote outputs stay in `.local`.
- **PER-005**: SSRF, secret-redaction, stale-price, and malformed-provider tests are mandatory.

### Key Entities

- **ResolvedAsset**: Safe source label, local copy, MIME, dimensions, size, and SHA-256.
- **BackendCapability**: Media/input modes, dimensions, alpha, stability, and cost type.
- **ProviderModelEntry**: Provider, pinned snapshot, terms, capability, pricing, and key name.
- **PricingEntry**: Effective/review dates and token or image-output rates.

## Success Criteria

- **SC-001**: 100% of private-network and invalid-media fixtures fail before backend use.
- **SC-002**: Routing chooses a qualified local backend whenever it satisfies the intent.
- **SC-003**: Fake generate and edit calls preserve complete cost and provenance evidence.
- **SC-004**: No test output contains the configured API key or URL query/fragment.

## Assumptions

- Organization verification and provider terms remain user actions.
- Automated tests never make paid requests.
