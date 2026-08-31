# Adapter registry

One JSON `AdapterRecord` per candidate/promoted LoRA records base-model hash, dataset
DVC revision, effective trainer configuration, adapter hash, evaluation evidence,
license lane and promotion state. Weight bytes are stored through DVC, not Git.
