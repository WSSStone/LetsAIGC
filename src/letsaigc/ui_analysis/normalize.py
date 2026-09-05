from io import BytesIO

import numpy as np
from PIL import Image, ImageCms, ImageOps

from ..assets.resolver import validate_image_bytes
from ..assets.store import ArtifactStore
from ..schemas.pipeline import ArtifactRef
from ..schemas.ui import CanonicalImage, ImageView, UIResourceLimits
from .coordinates import exif_matrix


def png(image: Image.Image) -> bytes:
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def validate_ui_image(content: bytes, media_type: str, limits: UIResourceLimits):
    if not content or len(content) > limits.max_image_bytes:
        raise ValueError("Input exceeds image byte limit")
    # Check UI's tighter geometry before the shared decoder materializes the image.
    with Image.open(BytesIO(content)) as image:
        if max(image.size) > limits.max_image_edge or image.width * image.height > limits.max_pixels:
            raise ValueError("Input exceeds image dimensions")
        stride = limits.ocr_tile_size - limits.ocr_tile_overlap
        tiles = [1 + max(0, (edge - limits.ocr_tile_size + stride - 1) // stride) for edge in image.size]
        if tiles[0] * tiles[1] > limits.ocr_max_tiles:
            raise ValueError("Image requires too many OCR tiles")
    return validate_image_bytes(content, media_type)


def normalize(store: ArtifactStore, source: ArtifactRef, operation: str, limits: UIResourceLimits):
    content = store.read(source)
    validate_ui_image(content, source.media_type, limits)
    with Image.open(BytesIO(content)) as image:
        size = image.size
        orientation = image.getexif().get(274, 1)
        matrix = exif_matrix(orientation, *size)
        alpha = "A" in image.getbands() or "transparency" in image.info
        canonical = ImageOps.exif_transpose(image).convert("RGBA" if alpha else "RGB")
        color_policy = "assumed_srgb_no_embedded_profile"
        if image.info.get("icc_profile"):
            profile = ImageCms.ImageCmsProfile(BytesIO(image.info["icc_profile"]))
            rgb = ImageCms.profileToProfile(canonical.convert("RGB"), profile, ImageCms.createProfile("sRGB"))
            if alpha:
                rgb.putalpha(canonical.getchannel("A"))
            canonical = rgb
            color_policy = "embedded_profile_converted_to_srgb"
        canonical.info.clear()
    ref = store.put(
        source.task_id,
        operation,
        png(canonical),
        role="canonical",
        media_type="image/png",
        source_ids=[source.artifact_id],
    )
    result = CanonicalImage(
        source_id="source-" + source.sha256[:32],
        original_ref=source,
        canonical_ref=ref,
        width=canonical.width,
        height=canonical.height,
        original_orientation=orientation,
    )
    return result, {
        "schema_version": 1,
        "original_size": list(size),
        "canonical_size": list(canonical.size),
        "original_to_canonical": matrix,
        "canonical_to_original": np.linalg.inv(matrix).tolist(),
        "mode": canonical.mode,
        "color_policy": color_policy,
        "alpha_rule": "preserve",
    }


def make_views(store: ArtifactStore, canonical: CanonicalImage, operation: str, limits: UIResourceLimits):
    size, overlap = limits.ocr_tile_size, limits.ocr_tile_overlap

    def origins(length):
        values = [0]
        while values[-1] + size < length:
            values.append(values[-1] + size - overlap)
        return values

    xs, ys = origins(canonical.width), origins(canonical.height)
    if len(xs) * len(ys) > limits.ocr_max_tiles:
        raise ValueError("Image requires too many OCR tiles")
    with Image.open(BytesIO(store.read(canonical.canonical_ref))) as loaded:
        image = loaded.copy()
    result = []

    def add(view_id, kind, box, pixels):
        sx = pixels.width / (box[2] - box[0])
        sy = pixels.height / (box[3] - box[1])
        forward = [[sx, 0, -box[0] * sx], [0, sy, -box[1] * sy], [0, 0, 1]]
        ref = store.put(
            canonical.canonical_ref.task_id,
            operation,
            png(pixels),
            role="view",
            media_type="image/png",
            source_ids=[canonical.canonical_ref.artifact_id],
        )
        result.append(
            ImageView(
                view_id=view_id,
                kind=kind,
                canonical_ref=canonical.canonical_ref,
                input_ref=ref,
                crop=box,
                width=pixels.width,
                height=pixels.height,
                forward=forward,
                inverse=np.linalg.inv(forward).tolist(),
            )
        )

    overview = image.copy()
    overview.thumbnail((limits.vlm_longest_edge, limits.vlm_longest_edge), Image.Resampling.LANCZOS)
    add("overview", "overview", (0, 0, image.width, image.height), overview)
    for y in ys:
        for x in xs:
            box = (x, y, min(image.width, x + size), min(image.height, y + size))
            add(f"tile-{x}-{y}", "ocr_tile", box, image.crop(box))
    return result
