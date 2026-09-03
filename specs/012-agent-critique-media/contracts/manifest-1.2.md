# RunManifest 1.2 Compatibility Contract

Readers accept schema versions 1.0, 1.1, and 1.2. Version 1.2 adds `agent_task` and
`remote_image_generation` kinds plus optional Agent session/task/iteration, provider
request/snapshot/redacted hash, input hashes, approval, budget, critic, stop reason,
and parent-child lineage evidence. Older records are not rewritten.
