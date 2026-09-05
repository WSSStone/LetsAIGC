"""Geometry-based layout fusion; model links remain suggestions, OCR remains original."""

from ..agent.ui_analyzer import validate_analysis
from ..schemas.pipeline import digest
from .coordinates import checked_box, checked_polygon, iou, map_box


def polygon_area(points):
    return abs(sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(points, points[1:] + points[:1], strict=True))) / 2


def covered_fraction(polygon, box):
    """Clip a text polygon to a control rectangle, preserving the actual polygon area."""
    denominator = polygon_area(polygon)
    if not denominator:
        return 0.0
    points = list(polygon)
    for axis, edge, greater in ((0, box[0], True), (0, box[2], False), (1, box[1], True), (1, box[3], False)):
        clipped = []
        for previous, current in zip(points[-1:] + points[:-1], points, strict=True):
            old_in = previous[axis] >= edge if greater else previous[axis] <= edge
            new_in = current[axis] >= edge if greater else current[axis] <= edge
            if old_in != new_in:
                fraction = (edge - previous[axis]) / (current[axis] - previous[axis])
                clipped.append(tuple(previous[i] + fraction * (current[i] - previous[i]) for i in (0, 1)))
            if new_in:
                clipped.append(current)
        points = clipped
        if not points:
            return 0.0
    return polygon_area(points) / denominator


def build_layout(canonical, view, texts, analysis, *, previous=None, revision=0):
    if type(revision) is not int or not 0 <= revision <= 2 or view.canonical_ref != canonical.canonical_ref:
        raise ValueError("Layout revision and canonical view identity must match")
    text_ids = {text["text_id"] for text in texts["texts"]}
    if len(text_ids) != len(texts["texts"]):
        raise ValueError("OCR IDs must be unique")
    for text in texts["texts"]:
        checked_polygon(text["polygon"], canonical.width, canonical.height)
    analysis = validate_analysis(
        analysis, width=view.width, height=view.height, text_ids=text_ids, view_ids={view.view_id}
    )
    candidates, aliases = [], {}
    for item in sorted(analysis["elements"], key=lambda element: (element["kind"], element["bbox"], element["id"])):
        box = list(map_box(item["bbox"], view.inverse))
        checked_box(box, canonical.width, canonical.height)
        duplicate = next(
            (old for old in candidates if old["kind"] == item["kind"] and iou(old["bbox"], box) >= 0.85), None
        )
        if duplicate:
            aliases[item["id"]] = duplicate["id"]
        else:
            candidates.append({**item, "bbox": box})
            aliases[item["id"]] = item["id"]
    if previous and previous["canonical_sha256"] != canonical.canonical_ref.sha256:
        raise ValueError("Revision must preserve the original canonical identity")
    old_elements = previous["elements"] if previous else []
    used, mapping, elements = set(), {}, []
    for item in candidates:
        matches = sorted(
            [
                (iou(item["bbox"], old["bbox"]), old["element_id"])
                for old in old_elements
                if old["kind"] == item["kind"]
                and old["element_id"] not in used
                and iou(item["bbox"], old["bbox"]) >= 0.5
            ],
            reverse=True,
        )
        if matches and (len(matches) == 1 or abs(matches[0][0] - matches[1][0]) > 1e-9):
            identity = matches[0][1]
            used.add(identity)
        else:
            identity = "element-" + digest([canonical.canonical_ref.sha256, item["kind"], item["bbox"], revision])[:32]
        mapping[item["id"]] = identity
        elements.append(
            {
                "element_id": identity,
                "kind": item["kind"],
                "bbox": item["bbox"],
                "parent_id": item["parent_id"],
                "text_ids": [],
                "evidence_ids": item["evidence_ids"],
            }
        )
    mapping = {identity: mapping[target] for identity, target in aliases.items()}
    for element in elements:
        parent = mapping.get(element["parent_id"])
        element["parent_id"] = parent if parent != element["element_id"] else None
    by_id = {element["element_id"]: element for element in elements}
    for element in elements:
        seen, parent = {element["element_id"]}, element["parent_id"]
        while parent:
            if parent in seen or parent not in by_id:
                raise ValueError("Deduplicated hierarchy is ambiguous")
            seen.add(parent)
            parent = by_id[parent]["parent_id"]
    unassigned = []
    for text in texts["texts"]:
        containing = sorted(
            [
                (
                    (element["bbox"][2] - element["bbox"][0]) * (element["bbox"][3] - element["bbox"][1]),
                    element["element_id"],
                )
                for element in elements
                if covered_fraction(text["polygon"], element["bbox"]) >= 0.8
            ]
        )
        if containing and (len(containing) == 1 or containing[0][0] != containing[1][0]):
            by_id[containing[0][1]]["text_ids"].append(text["text_id"])
        else:
            unassigned.append(text["text_id"])
    revision_mapping = {
        old["element_id"]: [
            element["element_id"]
            for element in elements
            if element["kind"] == old["kind"] and iou(element["bbox"], old["bbox"]) >= 0.5
        ]
        for old in old_elements
    }
    return {
        "schema_version": 1,
        "source_id": canonical.source_id,
        "canonical_ref": canonical.canonical_ref.model_dump(mode="json"),
        "canonical_sha256": canonical.canonical_ref.sha256,
        "width": canonical.width,
        "height": canonical.height,
        "coordinate_space": "canonical_px_xyxy_exclusive_max",
        "revision_id": revision,
        "elements": elements,
        "candidate_mapping": mapping,
        "revision_mapping": revision_mapping,
        "unassigned_text_ids": unassigned,
        "observations": analysis["observations"],
        "hypotheses": analysis["hypotheses"],
        "occlusions": [
            {**item, "front_id": mapping[item["front_id"]], "behind_id": mapping[item["behind_id"]]}
            for item in analysis["occlusions"]
            if mapping[item["front_id"]] != mapping[item["behind_id"]]
        ],
        "quality": {
            "status": "pending",
            "element_count": len(elements),
            "text_count": len(texts["texts"]),
            "unassigned_text_count": len(unassigned),
        },
    }
