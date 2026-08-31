from __future__ import annotations

import time
from typing import Any

from .config import load_evaluation_suite
from .errors import ValidationError
from .paths import find_repo_root
from .tracking.mlflow_store import log_manifest
from .workflows import WorkflowRunner


def run_evaluation(suite_id: str, runner: WorkflowRunner | None = None) -> dict[str, Any]:
    path = find_repo_root() / "configs/eval" / f"{suite_id}.yaml"
    if not path.is_file():
        raise ValidationError(f"Unknown evaluation suite: {suite_id}")
    suite = load_evaluation_suite(suite_id)
    workflow = suite.workflow
    seed = suite.fixed_seed
    executor = runner or WorkflowRunner()
    results = []
    start = time.monotonic()
    for case in suite.cases:
        case_start = time.monotonic()
        run = executor.run(workflow, {"prompt": case.prompt, "seed": seed})
        results.append(
            {
                "case": case.id,
                "run_id": run["run_id"],
                "elapsed_seconds": round(time.monotonic() - case_start, 3),
                "status": run["status"],
                "human_review": {name: None for name in suite.human_review},
            }
        )
    total = round(time.monotonic() - start, 3)
    mlflow_id = log_manifest(
        f"evaluation-{suite_id}",
        {"suite": suite_id, "workflow": workflow, "seed": seed},
        {"elapsed_seconds": total, "completed_cases": float(len(results))},
    )
    return {
        "suite": suite_id,
        "elapsed_seconds": total,
        "mlflow_run_id": mlflow_id,
        "results": results,
    }
