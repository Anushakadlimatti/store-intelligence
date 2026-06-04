from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


def iou(box_a: Tuple[float, float, float, float], box_b: Tuple[float, float, float, float]) -> float:
    ax1, ay1, ax2, ay2 = box_a
    bx1, by1, bx2, by2 = box_b
    inter_x1 = max(ax1, bx1)
    inter_y1 = max(ay1, by1)
    inter_x2 = min(ax2, bx2)
    inter_y2 = min(ay2, by2)
    if inter_x2 <= inter_x1 or inter_y2 <= inter_y1:
        return 0.0
    inter = (inter_x2 - inter_x1) * (inter_y2 - inter_y1)
    area_a = (ax2 - ax1) * (ay2 - ay1)
    area_b = (bx2 - bx1) * (by2 - by1)
    union = area_a + area_b - inter
    return inter / union if union > 0 else 0.0


@dataclass
class Track:
    track_id: int
    bbox: Tuple[float, float, float, float]
    centroid: Tuple[float, float]
    last_seen_frame: int = 0
    hits: int = 0
    visitor_id: str = ""
    zones_inside: set = field(default_factory=set)
    zone_enter_frame: Dict[str, int] = field(default_factory=dict)
    session_seq: int = 0
    crossed_entry: bool = False
    exited: bool = False
    is_staff: bool = False
    last_side: Optional[str] = None
    in_billing: bool = False
    billing_join_frame: Optional[int] = None
    history_centroids: List[Tuple[float, float]] = field(default_factory=list)


class SimpleTracker:
    def __init__(
        self,
        iou_threshold: float = 0.3,
        max_age: int = 30,
        visitor_id_for_new_track=None,
        on_track_removed=None,
    ):
        self.iou_threshold = iou_threshold
        self.max_age = max_age
        self.tracks: Dict[int, Track] = {}
        self._next_id = 1
        self._visitor_counter = 0
        self._visitor_id_for_new_track = visitor_id_for_new_track
        self._on_track_removed = on_track_removed

    def _new_visitor_id(self) -> str:
        self._visitor_counter += 1
        return f"VIS_{self._visitor_counter:06d}"

    def update(
        self,
        detections: List[Tuple[Tuple[float, float, float, float], float]],
        frame_idx: int,
        frame_w: int,
        frame_h: int,
    ) -> List[Track]:
        matched: Dict[int, Tuple] = {}
        used_dets = set()

        for tid, track in list(self.tracks.items()):
            best_iou = 0.0
            best_j = -1
            for j, (bbox, conf) in enumerate(detections):
                if j in used_dets:
                    continue
                score = iou(track.bbox, bbox)
                if score > best_iou and score >= self.iou_threshold:
                    best_iou = score
                    best_j = j
            if best_j >= 0:
                bbox, conf = detections[best_j]
                cx = (bbox[0] + bbox[2]) / 2
                cy = (bbox[1] + bbox[3]) / 2
                track.bbox = bbox
                track.centroid = (cx / frame_w, cy / frame_h)
                track.last_seen_frame = frame_idx
                track.hits += 1
                track.history_centroids.append(track.centroid)
                if len(track.history_centroids) > 20:
                    track.history_centroids.pop(0)
                matched[tid] = (bbox, conf)
                used_dets.add(best_j)

        for j, (bbox, conf) in enumerate(detections):
            if j in used_dets:
                continue
            tid = self._next_id
            self._next_id += 1
            cx = (bbox[0] + bbox[2]) / 2
            cy = (bbox[1] + bbox[3]) / 2
            if self._visitor_id_for_new_track is not None:
                visitor_id = self._visitor_id_for_new_track()
                if not visitor_id:
                    continue
            else:
                visitor_id = self._new_visitor_id()
            self.tracks[tid] = Track(
                track_id=tid,
                bbox=bbox,
                centroid=(cx / frame_w, cy / frame_h),
                last_seen_frame=frame_idx,
                hits=1,
                visitor_id=visitor_id,
                history_centroids=[(cx / frame_w, cy / frame_h)],
            )

        stale = [
            tid
            for tid, t in self.tracks.items()
            if frame_idx - t.last_seen_frame > self.max_age
        ]
        for tid in stale:
            track = self.tracks[tid]
            if self._on_track_removed:
                self._on_track_removed(track.visitor_id)
            del self.tracks[tid]

        return list(self.tracks.values())
