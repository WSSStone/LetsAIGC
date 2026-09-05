from io import BytesIO

from PIL import Image, ImageDraw

from .coordinates import checked_box
from .normalize import png


def create_crops(store, canonical, layout, operation_id, *, resources=None):
    if layout["canonical_sha256"] != canonical.canonical_ref.sha256:
        raise ValueError("Layout must reference this canonical image")
    boxes = [
        (element, checked_box(element["bbox"], canonical.width, canonical.height)) for element in layout["elements"]
    ]
    if resources is not None:
        from .resources import check_storage

        extra = sum((box[2] - box[0]) * (box[3] - box[1]) * 5 for _, box in boxes)
        extra += canonical.width * canonical.height * 4
        check_storage(
            store,
            canonical.canonical_ref.task_id,
            resources,
            extra_bytes=extra,
            memory_bytes=canonical.width * canonical.height * 20,
        )
    with Image.open(BytesIO(store.read(canonical.canonical_ref))) as loaded:
        image = loaded.copy()
    if image.size != (canonical.width, canonical.height):
        raise ValueError("Canonical dimensions changed")
    overlay = image.convert("RGB")
    drawing = ImageDraw.Draw(overlay)
    crops = []
    for index, (element, box) in enumerate(boxes, 1):
        ref = store.put(
            canonical.canonical_ref.task_id,
            operation_id,
            png(image.crop(box)),
            role="rect_crop",
            media_type="image/png",
            source_ids=[canonical.canonical_ref.artifact_id],
        )
        crops.append(
            {"element_id": element["element_id"], "ref": ref, "bbox": list(box), "epistemic_status": "observed"}
        )
        drawing.rectangle((box[0], box[1], box[2] - 1, box[3] - 1), outline=(255, 199, 24), width=2)
        drawing.text((box[0] + 2, box[1] + 2), str(index), fill=(255, 255, 255), stroke_width=1, stroke_fill=(0, 0, 0))
    overlay_ref = store.put(
        canonical.canonical_ref.task_id,
        operation_id,
        png(overlay),
        role="overlay",
        media_type="image/png",
        source_ids=[canonical.canonical_ref.artifact_id],
    )
    return {
        "schema_version": 1,
        "crops": crops,
        "overlay_ref": overlay_ref,
        "overlay_labels": {str(index): element["element_id"] for index, (element, _) in enumerate(boxes, 1)},
    }
