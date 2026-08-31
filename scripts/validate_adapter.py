from __future__ import annotations

import json
import sys
from pathlib import Path

import torch
from safetensors.torch import load_file


def main() -> int:
    adapter = Path(sys.argv[1])
    tensors = load_file(adapter)
    trainable = [tensor for name, tensor in tensors.items() if "lora_up" in name]
    nonfinite = sum((~torch.isfinite(tensor)).any().item() for tensor in tensors.values())
    updated = sum(torch.count_nonzero(tensor).item() for tensor in trainable)
    report = {
        "valid": bool(tensors) and bool(trainable) and nonfinite == 0 and updated > 0,
        "tensor_count": len(tensors),
        "trainable_tensor_count": len(trainable),
        "nonfinite_tensor_count": nonfinite,
        "updated_value_count": updated,
    }
    print(json.dumps(report))
    return 0 if report["valid"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
