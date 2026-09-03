# Quickstart: Video Runtime and Models

> **Historical expert entrypoint (2026-09-03):** These direct video/runpack commands
> remain available. Complete the core/ComfyUI setup in the
> [Agent guide](../../docs/agent-quickstart.md) before using the local video path.
> The validation record below covers the expert pipeline on 2026-08-31; Agent
> generation, approval, and critique have separate live acceptance tasks.

```powershell
# User-managed system tools must already be on PATH.
ffmpeg -version
ffprobe -version

mamba run -n letsaigc-core letsaigc --json doctor
mamba run -n letsaigc-core letsaigc models sync video-local-smoke

# With local ComfyUI running on 127.0.0.1:8188:
mamba run -n letsaigc-core letsaigc --json video run wan21-t2v-smoke --set prompt="game character walking on pure green background"

# Register an external clip with auditable metadata.
mamba run -n letsaigc-core letsaigc --json video import .\clip.mp4 --metadata .\clip.metadata.yaml

# Build for stronger hardware and later ingest its result.
mamba run -n letsaigc-core letsaigc --json runpack build --job configs\video\wan22-cloud-job.yaml
mamba run -n letsaigc-core letsaigc --json runpack ingest .local\runpacks\JOB_ID --result .\result.mp4 --metadata .\result.yaml
```

Hardware-dependent commands may report a missing model or unavailable ComfyUI until the operator completes model download and starts the locked runtime. CPU-only tests validate probing, imports and runpack integrity without those prerequisites.

## 2026-08-31 validation record

- System `ffmpeg` and `ffprobe` resolve to `D:\Programs\ffmpeg\bin`; PNG, H.264 decoding and
  `libx264` encoding were detected successfully.
- Schema, output-discovery, import/runpack, Sprite and two-shot drama tests run without GPU.
- The native Wan nodes and `SaveVideo` contract were checked against the locked ComfyUI
  `/object_info` surface.
- `video-local-smoke` was downloaded and independently verified against all three catalog hashes.
- The non-cached RTX 3080 smoke succeeded at 512×512/17 frames: 27.594 seconds, 9,625 MiB
  peak system GPU use, H.264/yuv420p, 16 FPS, output SHA-256
  `7af5a643d3d8c8506f58a39daf31c61ec6e76602547f860233cbf22b6a4ddfd7`.
- Native ComfyUI 0.34.2 reports `SaveVideo` MP4 artifacts through the history `images` field with
  `animated=true`; the contract is pinned to that observed shape.
- The first real Sprite derivative was correctly rejected after visual audit exposed green edge
  residue. Model/prompt iteration and explicit owner approval remain asset-level acceptance work.
