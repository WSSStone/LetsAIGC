# Implementation Plan: Workflow Runner and Evaluation

Implement typed workflow contracts and JSON-pointer bindings in the core package.
The runner validates inputs and dependencies before queueing, follows WebSocket and
history state, hashes outputs, and logs MLflow metadata. Evaluation composes fixed
workflow cases; review and export are separate commands so success never implies
production approval. A ZIP runpack serializes portable metadata and DVC pointers.

Constitution gates pass through workflow-as-code, local services, explicit license
lanes, immutable evidence, and a human promotion gate.
