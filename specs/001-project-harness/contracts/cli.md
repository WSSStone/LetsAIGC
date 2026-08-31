# CLI Contract

- `letsaigc [--json] COMMAND` emits a human summary or one stable JSON object.
- Exit 0 means success; 2 policy/validation; 3 dependency/readiness; 4 runtime failure.
- No command accepts a license, exposes a service, kills a process, or silently
  changes effective training parameters.

Commands: `doctor`; `bootstrap --component core|comfy|train`; `models list|verify|sync
PROFILE`; `comfy serve`; `workflow run ID [--set KEY=VALUE]`; `train sdxl-lora
--config FILE`; `eval run SUITE`; `export RUN_ID --to PATH --lane production`;
`runpack build RUN_ID`.
