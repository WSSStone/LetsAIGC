"""Canonical pixel geometry; right and lower box boundaries are exclusive."""

import math


def checked_box(box, width: int, height: int) -> tuple[int, int, int, int]:
    if len(box) != 4 or not all(math.isfinite(v) and not isinstance(v, bool) for v in box):
        raise ValueError("Invalid bounding box")
    x1, y1, x2, y2 = box
    if not (0 <= x1 < x2 <= width and 0 <= y1 < y2 <= height):
        raise ValueError("Bounding box is empty or outside canonical image")
    return math.floor(x1), math.floor(y1), math.ceil(x2), math.ceil(y2)


def checked_polygon(points, width: int, height: int):
    if not 3 <= len(points) <= 1024 or any(
        len(p) != 2 or any(not math.isfinite(v) or isinstance(v, bool) for v in p) for p in points
    ):
        raise ValueError("Invalid polygon")
    # PaddleOCR returns NumPy integer scalars. Promote them before multiplying
    # cross products so ordinary image coordinates cannot overflow NumPy ints.
    points = [(float(x), float(y)) for x, y in points]
    if any(not (0 <= x <= width and 0 <= y <= height) for x, y in points):
        raise ValueError("Polygon exceeds image bounds")
    if len({tuple(point) for point in points}) != len(points):
        raise ValueError("Polygon repeats a vertex")
    edges = list(zip(points, points[1:] + points[:1], strict=True))
    area = sum(a[0] * b[1] - b[0] * a[1] for a, b in edges)
    if abs(area) < 1e-6:
        raise ValueError("Zero-area polygon")

    def cross(a, b, c):
        return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])

    def on_segment(a, b, c):
        return abs(cross(a, b, c)) < 1e-9 and (
            min(a[0], b[0]) <= c[0] <= max(a[0], b[0]) and min(a[1], b[1]) <= c[1] <= max(a[1], b[1])
        )

    for i, (a, b) in enumerate(edges):
        for j, (c, d) in enumerate(edges):
            if j <= i + 1 or (i == 0 and j == len(edges) - 1):
                continue
            if (cross(a, b, c) * cross(a, b, d) < 0 and cross(c, d, a) * cross(c, d, b) < 0) or any(
                (on_segment(a, b, c), on_segment(a, b, d), on_segment(c, d, a), on_segment(c, d, b))
            ):
                raise ValueError("Self-intersecting polygon")
    return points


def map_point(point, matrix) -> tuple[float, float]:
    if len(point) != 2 or not all(math.isfinite(v) for v in point):
        raise ValueError("Invalid point")
    x, y = point
    mapped = [row[0] * x + row[1] * y + row[2] for row in matrix]
    if len(mapped) != 3 or not all(math.isfinite(v) for v in mapped) or abs(mapped[2]) < 1e-12:
        raise ValueError("Invalid coordinate transform")
    return mapped[0] / mapped[2], mapped[1] / mapped[2]


def map_box(box, matrix):
    x1, y1, x2, y2 = box
    points = [map_point(point, matrix) for point in ((x1, y1), (x2, y1), (x2, y2), (x1, y2))]
    return min(x for x, y in points), min(y for x, y in points), max(x for x, y in points), max(y for x, y in points)


def exif_matrix(orientation: int, width: int, height: int):
    matrices = {
        1: [[1, 0, 0], [0, 1, 0], [0, 0, 1]],
        2: [[-1, 0, width], [0, 1, 0], [0, 0, 1]],
        3: [[-1, 0, width], [0, -1, height], [0, 0, 1]],
        4: [[1, 0, 0], [0, -1, height], [0, 0, 1]],
        5: [[0, 1, 0], [1, 0, 0], [0, 0, 1]],
        6: [[0, -1, height], [1, 0, 0], [0, 0, 1]],
        7: [[0, -1, height], [-1, 0, width], [0, 0, 1]],
        8: [[0, 1, 0], [-1, 0, width], [0, 0, 1]],
    }
    if type(orientation) is not int or orientation not in matrices:
        raise ValueError("Invalid EXIF orientation")
    return matrices[orientation]


def iou(a, b) -> float:
    intersection = max(0, min(a[2], b[2]) - max(a[0], b[0])) * max(0, min(a[3], b[3]) - max(a[1], b[1]))
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - intersection
    return intersection / union if union > 0 else 0.0
