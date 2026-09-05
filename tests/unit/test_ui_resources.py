import pytest

from letsaigc.pipelines.errors import PipelineError
from letsaigc.schemas.ui import UIAnalysisRequest
from letsaigc.ui_analysis.resources import check_request_resources


def test_small_network_or_memory_ceiling_rejects_before_execution(ui_store):
    original = ui_store.put("resource-test", "input", b"image", role="original")
    request = UIAnalysisRequest(
        input={"kind": "manual", "inputs": [original]},
        budget={
            "max_total_cost_usd": 1,
            "max_iteration_cost_usd": 0.25,
            "max_total_gpu_minutes": 0,
            "max_iteration_gpu_minutes": 0,
            "max_revisions": 2,
        },
        resources={"network_bytes": 1},
    )
    with pytest.raises(PipelineError, match="resource_insufficient"):
        check_request_resources(ui_store, "resource-test", request)
    request = request.model_copy(
        update={"resources": request.resources.model_copy(update={"network_bytes": 1024**3, "ram_bytes": 1})}
    )
    with pytest.raises(PipelineError, match="resource_insufficient"):
        check_request_resources(ui_store, "resource-test", request)
