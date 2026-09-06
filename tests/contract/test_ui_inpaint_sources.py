"""Independent codec and provenance integration for masked compilation."""

import io
import json

import pytest
from PIL import Image, ImageOps
from test_ui_inpaint_safety import _compile, _fixture

from letsaigc.errors import ValidationError
from letsaigc.schemas.pipeline import canonical_json
from letsaigc.ui_analysis.selection import TrustedSource


def test_jpeg_with_exif_binds_original_hash_to_distinct_canonical_png(tmp_path):
    original = Image.new("RGB", (12, 16), (53, 73, 97))
    exif = Image.Exif()
    exif[274] = 6
    encoded = io.BytesIO()
    original.save(encoded, format="JPEG", exif=exif)
    with Image.open(io.BytesIO(encoded.getvalue())) as image:
        canonical = ImageOps.exif_transpose(image).convert("RGB")
        canonical.info.clear()
    plan, store, refs, *_ = _fixture(tmp_path, canonical=canonical)
    original_ref = store.put(plan.task_id, "jpeg", encoded.getvalue(), role="original", media_type="image/jpeg")
    selection = json.loads(store.read(refs["selection_ref"]))
    selection["sources"][0]["original_sha256"] = original_ref.sha256
    selection_ref = store.put(plan.task_id, "jpeg", canonical_json(selection).encode(), role="selection")
    binding = plan.image_mask.model_copy(update={
        "selection_ref": selection_ref, "selection_hash": selection_ref.sha256,
    })
    plan = plan.model_copy(update={"image_mask": binding})
    trusted = {"source-1": TrustedSource(
        "source-1", original_ref, canonical_ref=refs["canonical_ref"], layout_ref=refs["layout_ref"],
    )}
    assert original_ref.sha256 != refs["canonical_ref"].sha256
    result = _compile(plan, store, tmp_path, trusted)
    assert result.validation["masked"] is True

    wrong = store.put(plan.task_id, "jpeg", b"wrong original", role="original", media_type="image/jpeg")
    trusted["source-1"] = TrustedSource(
        "source-1", wrong, canonical_ref=refs["canonical_ref"], layout_ref=refs["layout_ref"],
    )
    with pytest.raises(ValidationError):
        _compile(plan, store, tmp_path, trusted)
