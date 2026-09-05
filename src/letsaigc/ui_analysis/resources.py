"""Conservative admission bounds for the preview's fixed sequential operations."""

import shutil

import psutil

from ..pipelines.errors import PipelineError


def check_storage(store, task_id, limits, *, extra_bytes=0, memory_bytes=0):
    root = store.root.resolve()
    directory = (root / task_id).resolve()
    if not directory.is_relative_to(root):
        raise PipelineError("artifact_scope")
    used = 0
    if directory.exists():
        for path in directory.rglob("*"):
            if path.is_file():
                if not path.resolve().is_relative_to(root):
                    raise PipelineError("artifact_scope")
                used += path.stat().st_size
                if used + extra_bytes > limits.temporary_bytes:
                    raise PipelineError("resource_insufficient", "resource_insufficient")
    ancestor = root
    while not ancestor.exists():
        ancestor = ancestor.parent
    if (
        used + extra_bytes > limits.temporary_bytes
        or shutil.disk_usage(ancestor).free < limits.minimum_free_disk_bytes + extra_bytes
        or psutil.Process().memory_info().rss + memory_bytes > limits.ram_bytes
    ):
        raise PipelineError("resource_insufficient", "resource_insufficient")


def check_request_resources(store, task_id, request):
    limits = request.resources
    # Reserve the maximum body volume of the allowed fixed calls up front.
    # This is intentionally conservative; user limits never become unbounded.
    network = (limits.ocr_max_tiles + request.limits.ocr_rereads_per_image) * 8 * 1024**2
    network += request.limits.vlm_calls_per_image * 16 * 1024**2
    if request.input.kind == "search":
        network += (4 + request.limits.search_attempts) * 2 * 1024**2
        network += request.limits.queries * request.limits.downloads_per_query * limits.max_image_bytes
    if network > limits.network_bytes:
        raise PipelineError("resource_insufficient", "resource_insufficient")
    check_storage(store, task_id, limits, memory_bytes=limits.max_pixels * 20)
