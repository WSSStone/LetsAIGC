# Implementation Plan: Sprite Sequence Pipeline

Use the `005` media boundary to extract timestamp-normalized PNG frames. A Pillow/NumPy processor converts sRGB to Lab, creates a feathered color-key Alpha matte, applies despill, computes per-frame Alpha bounds, uses a common contain scale, and stabilizes bottom-center anchors. A validator records warnings and failures before a deterministic near-square atlas packer writes PNG plus JSON. The derived run inherits source license lanes and lineage; direct inputs set provenance false.

Constitution gates pass: the profile declares temporal/disk limits; intermediate media stays local; every output is hashed; production still requires automated checks and human approval.

Source structure: `src/letsaigc/sprites/`, `configs/sprites/`, CLI wiring in `cli.py`, and unit/integration tests under `tests/`.

