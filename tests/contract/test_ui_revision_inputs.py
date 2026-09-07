"""Local model calls must use the frozen canonical crop."""

from io import BytesIO

import pytest
from PIL import Image
from test_ui_local_revision_budget import _local_child, _root

from letsaigc.pipelines.errors import PipelineError
from letsaigc.schemas.pipeline import ArtifactRef
from letsaigc.schemas.ui import ImageView
from letsaigc.ui_analysis.revision_inputs import LocalRevisionInputs, validate_local_request


@pytest.mark.parametrize("change", ["pixels", "transform", "dimensions"])
def test_local_revision_rejects_unrelated_pixels_or_geometry(tmp_path, change):
    service, root, original, image_bytes, selection = _root(tmp_path)
    child = _local_child(service, root, original, image_bytes, selection, "revision", "reread_text")
    wrapper = LocalRevisionInputs.model_validate_json(
        service.artifacts.read(ArtifactRef.model_validate(child.parameters["request_ref"]))
    )
    view = ImageView.model_validate_json(service.artifacts.read(wrapper.view_ref))
    if change == "pixels":
        stream = BytesIO()
        Image.new("RGB", (64, 48), "black").save(stream, format="PNG")
        other = service.artifacts.put(child.task_id, "other", stream.getvalue(), role="view")
        view = view.model_copy(update={"input_ref": other})
    elif change == "transform":
        view = view.model_copy(update={
            "forward": ((2., 0., 0.), (0., 2., 0.), (0., 0., 1.)),
            "inverse": ((.5, 0., 0.), (0., .5, 0.), (0., 0., 1.)),
        })
    else:
        view = view.model_copy(update={"width": 63})
    changed = service.artifacts.put(child.task_id, "other", view.model_dump_json().encode(),
                                    role="view_manifest")
    wrapper = wrapper.model_copy(update={"view_ref": changed})
    with pytest.raises(PipelineError) as caught:
        validate_local_request(service.artifacts, wrapper, task_id=child.task_id,
                               purpose="reread_text", selection_ref=wrapper.selection_ref,
                               selection_revision=wrapper.selection_revision)
    assert caught.value.code == "input_changed"
