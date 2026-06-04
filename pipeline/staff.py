from __future__ import annotations

from typing import Iterable, Sequence, Tuple

import cv2
import numpy as np

BBox = Tuple[float, float, float, float]

# Minimum people sharing the same upper-body dress signature to classify as staff.
STAFF_UNIFORM_MIN_COUNT = 2


def upper_body_dress_signature(frame: np.ndarray, bbox: BBox) -> tuple[int, ...] | None:
    """Quantized HSV color signature of the upper body (dress / uniform)."""
    x1, y1, x2, y2 = map(int, bbox)
    h, w = frame.shape[:2]
    x1, y1 = max(0, x1), max(0, y1)
    x2, y2 = min(w, x2), min(h, y2)
    if x2 <= x1 or y2 <= y1:
        return None
    crop = frame[y1 : y1 + max(1, (y2 - y1) // 2), x1:x2]
    if crop.size == 0:
        return None
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    # 3x2 grid of mean H,S,V bins (uniform = stable signature across people)
    gh, gw = 3, 2
    cell_h = max(1, hsv.shape[0] // gh)
    cell_w = max(1, hsv.shape[1] // gw)
    bins: list[int] = []
    for gy in range(gh):
        for gx in range(gw):
            cell = hsv[
                gy * cell_h : min((gy + 1) * cell_h, hsv.shape[0]),
                gx * cell_w : min((gx + 1) * cell_w, hsv.shape[1]),
            ]
            if cell.size == 0:
                continue
            mean = cell.reshape(-1, 3).mean(axis=0)
            bins.extend(
                (
                    int(mean[0] // 30),  # H: 6 bins
                    int(mean[1] // 64),  # S: 4 bins
                    int(mean[2] // 64),  # V: 4 bins
                )
            )
    return tuple(bins) if bins else None


def _signature_distance(a: tuple[int, ...], b: tuple[int, ...]) -> int:
    if len(a) != len(b):
        return 999
    return sum(abs(x - y) for x, y in zip(a, b))


def _cluster_signatures(
    signatures: list[tuple[int, ...] | None],
    max_distance: int = 8,
) -> list[list[int]]:
    """Group detection indices with similar dress signatures."""
    clusters: list[list[int]] = []
    for i, sig in enumerate(signatures):
        if sig is None:
            continue
        placed = False
        for cluster in clusters:
            rep = signatures[cluster[0]]
            if rep is not None and _signature_distance(sig, rep) <= max_distance:
                cluster.append(i)
                placed = True
                break
        if not placed:
            clusters.append([i])
    return clusters


def staff_indices_from_dress_code(
    frame: np.ndarray,
    bboxes: Sequence[BBox],
    min_count: int = STAFF_UNIFORM_MIN_COUNT,
) -> set[int]:
    """
    If >= min_count people share the same dress code (upper-body colors), treat them as staff.
    Returns indices into bboxes.
    """
    if len(bboxes) < min_count:
        return set()

    signatures = [upper_body_dress_signature(frame, b) for b in bboxes]
    staff: set[int] = set()
    for cluster in _cluster_signatures(signatures):
        if len(cluster) >= min_count:
            staff.update(cluster)
    return staff


def filter_non_staff_detections(
    frame: np.ndarray,
    detections: Iterable[Tuple[BBox, float]],
    min_count: int = STAFF_UNIFORM_MIN_COUNT,
) -> list[Tuple[BBox, float]]:
    dets = list(detections)
    if not dets:
        return []
    bboxes = [d[0] for d in dets]
    staff = staff_indices_from_dress_code(frame, bboxes, min_count=min_count)
    return [d for i, d in enumerate(dets) if i not in staff]
