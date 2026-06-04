from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Callable, Dict, List, Optional, Tuple

import cv2
import httpx
import numpy as np

from app.models import EventType, StoreEvent
from pipeline.emit import clip_timestamp, make_event
from pipeline.layout import ROOT, get_store
from pipeline.staff import filter_non_staff_detections
from pipeline.tracker import SimpleTracker
from pipeline.visitor_pool import StoreVisitorPool

_CAMERA_ORDER = {"entry": 0, "floor": 1, "billing": 2}


def load_yolo():
    from ultralytics import YOLO

    model = YOLO("yolov8n.pt")
    return model


def detect_people(
    model,
    frame: np.ndarray,
    conf: float = 0.35,
    inference_lock: Optional[RLock] = None,
) -> List[Tuple[Tuple[float, float, float, float], float]]:
    if inference_lock is not None:
        with inference_lock:
            results = model(frame, classes=[0], verbose=False, conf=conf)
    else:
        results = model(frame, classes=[0], verbose=False, conf=conf)
    dets = []
    for r in results:
        if r.boxes is None:
            continue
        for box in r.boxes:
            x1, y1, x2, y2 = box.xyxy[0].cpu().numpy()
            c = float(box.conf[0])
            dets.append(((float(x1), float(y1), float(x2), float(y2)), c))
    return dets


def centroid_in_roi(cx: float, cy: float, roi: dict) -> bool:
    return roi["x1"] <= cx <= roi["x2"] and roi["y1"] <= cy <= roi["y2"]


def process_entry_camera(
    track,
    camera_rois: dict,
    frame_idx: int,
    events: List[StoreEvent],
    store_id: str,
    camera_id: str,
    clip_start: datetime,
    fps: float,
    frame_w: int,
    entered_visitors: set[str],
    visitor_pool: Optional[StoreVisitorPool] = None,
    state_lock: Optional[RLock] = None,
):
    line = camera_rois.get("crossing_line")
    if not line:
        return
    lx = (line["x1"] + line["x2"]) / 2
    cx, cy = track.centroid
    side = "left" if cx < lx else "right"
    if track.last_side is None:
        track.last_side = side
        return
    if track.last_side != side:
        ts = clip_timestamp(clip_start, frame_idx, fps)
        if side == "right" and not track.crossed_entry:
            track.crossed_entry = True
            track.session_seq += 1
            if track.visitor_id not in entered_visitors:
                if state_lock is not None:
                    with state_lock:
                        entered_visitors.add(track.visitor_id)
                        if visitor_pool is not None:
                            visitor_pool.register_entry(track.visitor_id)
                else:
                    entered_visitors.add(track.visitor_id)
                    if visitor_pool is not None:
                        visitor_pool.register_entry(track.visitor_id)
                events.append(
                    make_event(
                        store_id,
                        camera_id,
                        track.visitor_id,
                        EventType.ENTRY,
                        ts,
                        zone_id="ENTRANCE",
                        confidence=0.9,
                        session_seq=track.session_seq,
                    )
                )
        elif side == "left" and track.crossed_entry and not track.exited:
            track.exited = True
            events.append(
                make_event(
                    store_id,
                    camera_id,
                    track.visitor_id,
                    EventType.EXIT,
                    ts,
                    zone_id="ENTRANCE",
                    confidence=0.88,
                    session_seq=track.session_seq,
                )
            )
        track.last_side = side


def process_zone_camera(
    track,
    camera_rois: dict,
    frame_idx: int,
    events: List[StoreEvent],
    store_id: str,
    camera_id: str,
    clip_start: datetime,
    fps: float,
    dwell_interval_frames: int,
    fps_val: float,
):
    cx, cy = track.centroid
    inside_now: list[str] = []
    for zone_id, roi in camera_rois.items():
        if zone_id == "crossing_line":
            continue
        if centroid_in_roi(cx, cy, roi):
            inside_now.append(zone_id)
    # One shelf zone at a time — avoids duplicate counts when ROIs overlap
    if len(inside_now) > 1:
        inside_now = [inside_now[0]]

    for zone_id in list(track.zones_inside):
        if zone_id not in inside_now:
            enter_f = track.zone_enter_frame.get(zone_id, frame_idx)
            dwell_ms = int((frame_idx - enter_f) / fps_val * 1000)
            track.zones_inside.discard(zone_id)
            track.session_seq += 1
            events.append(
                make_event(
                    store_id,
                    camera_id,
                    track.visitor_id,
                    EventType.ZONE_EXIT,
                    clip_timestamp(clip_start, frame_idx, fps_val),
                    zone_id=zone_id,
                    dwell_ms=0,
                    confidence=0.8,
                    session_seq=track.session_seq,
                )
            )
            if dwell_ms >= 30000:
                events.append(
                    make_event(
                        store_id,
                        camera_id,
                        track.visitor_id,
                        EventType.ZONE_DWELL,
                        clip_timestamp(clip_start, frame_idx, fps_val),
                        zone_id=zone_id,
                        dwell_ms=dwell_ms,
                        confidence=0.78,
                        session_seq=track.session_seq,
                    )
                )

    for zone_id in inside_now:
        inside = True
        if inside and zone_id not in track.zones_inside:
            track.zones_inside.add(zone_id)
            track.zone_enter_frame[zone_id] = frame_idx
            track.session_seq += 1
            events.append(
                make_event(
                    store_id,
                    camera_id,
                    track.visitor_id,
                    EventType.ZONE_ENTER,
                    clip_timestamp(clip_start, frame_idx, fps_val),
                    zone_id=zone_id,
                    confidence=0.82,
                    session_seq=track.session_seq,
                )
            )
        elif inside and zone_id in track.zones_inside:
            enter_f = track.zone_enter_frame.get(zone_id, frame_idx)
            elapsed_ms = int((frame_idx - enter_f) / fps_val * 1000)
            if elapsed_ms > 0 and elapsed_ms % 30000 < int(1000 / fps_val * 2):
                events.append(
                    make_event(
                        store_id,
                        camera_id,
                        track.visitor_id,
                        EventType.ZONE_DWELL,
                        clip_timestamp(clip_start, frame_idx, fps_val),
                        zone_id=zone_id,
                        dwell_ms=30000,
                        confidence=0.75,
                        session_seq=track.session_seq,
                    )
                )


def process_billing_camera(
    track,
    camera_rois: dict,
    frame_idx: int,
    events: List[StoreEvent],
    store_id: str,
    camera_id: str,
    clip_start: datetime,
    fps: float,
    queue_depth: int,
    billing_in_queue: set[str],
    state_lock: Optional[RLock] = None,
):
    queue_roi = camera_rois.get(
        "BILLING_QUEUE",
        camera_rois.get("BILLING_CHECKOUT", camera_rois.get("CASH_COUNTER")),
    )
    if not queue_roi:
        return
    cx, cy = track.centroid
    inside = centroid_in_roi(cx, cy, queue_roi)
    vid = track.visitor_id
    if inside and vid not in billing_in_queue:
        if state_lock is not None:
            with state_lock:
                billing_in_queue.add(vid)
        else:
            billing_in_queue.add(vid)
        track.in_billing = True
        track.billing_join_frame = frame_idx
        track.session_seq += 1
        events.append(
            make_event(
                store_id,
                camera_id,
                track.visitor_id,
                EventType.BILLING_QUEUE_JOIN,
                clip_timestamp(clip_start, frame_idx, fps),
                zone_id="BILLING_QUEUE",
                confidence=0.8,
                metadata={"queue_depth": queue_depth},
                session_seq=track.session_seq,
            )
        )
    elif not inside and vid in billing_in_queue:
        if state_lock is not None:
            with state_lock:
                billing_in_queue.discard(vid)
        else:
            billing_in_queue.discard(vid)
        track.in_billing = False
        track.session_seq += 1
        events.append(
            make_event(
                store_id,
                camera_id,
                vid,
                EventType.BILLING_QUEUE_ABANDON,
                clip_timestamp(clip_start, frame_idx, fps),
                zone_id="BILLING_QUEUE",
                confidence=0.72,
                session_seq=track.session_seq,
            )
        )


def process_video(
    store_id: str,
    camera: dict,
    model,
    sample_fps: float = 2.0,
    max_frames: Optional[int] = None,
    entered_visitors: Optional[set[str]] = None,
    visitor_pool: Optional[StoreVisitorPool] = None,
    billing_in_queue: Optional[set[str]] = None,
    on_event: Optional[Callable[[StoreEvent], None]] = None,
    playback_realtime: bool = False,
    inference_lock: Optional[RLock] = None,
    state_lock: Optional[RLock] = None,
) -> List[StoreEvent]:
    video_path = ROOT / camera["video_path"]
    if not video_path.exists():
        print(f"Missing video: {video_path}", file=sys.stderr)
        return []

    store = get_store(store_id)
    camera_id = camera["camera_id"]
    role = camera["role"]
    rois = store.get("camera_zone_rois", {}).get(camera_id, {})

    cap = cv2.VideoCapture(str(video_path))
    src_fps = cap.get(cv2.CAP_PROP_FPS) or 15.0
    frame_skip = max(1, int(src_fps / sample_fps))
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

    clip_start = datetime(2026, 4, 10, 12, 0, 0, tzinfo=timezone.utc)
    entered_visitors = entered_visitors if entered_visitors is not None else set()
    billing_in_queue = billing_in_queue if billing_in_queue is not None else set()
    if role != "entry" and visitor_pool is not None:
        tracker = SimpleTracker(
            visitor_id_for_new_track=visitor_pool.borrow_for_new_track,
            on_track_removed=visitor_pool.release,
        )
    else:
        tracker = SimpleTracker()
    events: List[StoreEvent] = []
    frame_idx = 0
    processed = 0
    emitted = 0

    def _flush_new_events() -> None:
        nonlocal emitted
        if not on_event:
            return
        while emitted < len(events):
            on_event(events[emitted])
            emitted += 1

    while True:
        ret, frame = cap.read()
        if not ret:
            break
        if frame_idx % frame_skip != 0:
            frame_idx += 1
            continue

        dets = filter_non_staff_detections(
            frame, detect_people(model, frame, inference_lock=inference_lock)
        )

        tracks = tracker.update(dets, frame_idx, w, h)
        queue_depth = sum(1 for t in tracks if t.in_billing and not t.is_staff)

        for track in tracks:
            if track.is_staff:
                continue

            if role == "entry":
                process_entry_camera(
                    track,
                    rois,
                    frame_idx,
                    events,
                    store_id,
                    camera_id,
                    clip_start,
                    src_fps,
                    w,
                    entered_visitors,
                    visitor_pool,
                    state_lock=state_lock,
                )
            elif role == "floor":
                process_zone_camera(
                    track, rois, frame_idx, events, store_id, camera_id, clip_start, src_fps, 30, src_fps
                )
            elif role == "billing":
                process_billing_camera(
                    track,
                    rois,
                    frame_idx,
                    events,
                    store_id,
                    camera_id,
                    clip_start,
                    src_fps,
                    queue_depth,
                    billing_in_queue,
                    state_lock=state_lock,
                )

        _flush_new_events()
        if playback_realtime:
            time.sleep(max(0.05, 1.0 / sample_fps))

        processed += 1
        if max_frames and processed >= max_frames:
            break
        frame_idx += 1

    cap.release()
    _flush_new_events()
    return events


def ingest_to_api(events: List[StoreEvent], api_url: str, batch_size: int = 100) -> None:
    payload_events = [e.model_dump(mode="json") for e in events]
    for i in range(0, len(payload_events), batch_size):
        batch = payload_events[i : i + batch_size]
        with httpx.Client(timeout=60.0) as client:
            r = client.post(f"{api_url.rstrip('/')}/events/ingest", json={"events": batch})
            r.raise_for_status()
            print(f"Ingested batch {i // batch_size + 1}: {r.json()}")


def main():
    parser = argparse.ArgumentParser(description="Store Intelligence detection pipeline")
    parser.add_argument("--store", default=None, help="STORE_1 or STORE_2")
    parser.add_argument("--camera", default=None, help="Specific camera_id")
    parser.add_argument("--output", default="output/events.jsonl")
    parser.add_argument("--api-url", default=None, help="POST events to API")
    parser.add_argument("--sample-fps", type=float, default=2.0)
    parser.add_argument("--max-frames", type=int, default=None)
    parser.add_argument("--fast", action="store_true", help="Limit frames for quick run")
    args = parser.parse_args()

    max_frames = 120 if args.fast else args.max_frames
    model = load_yolo()

    layout = json.loads((ROOT / "store_layout.json").read_text())
    all_events: List[StoreEvent] = []

    for store in layout["stores"]:
        store_id = store["store_id"]
        if args.store and store_id != args.store:
            continue
        cameras = sorted(
            store["cameras"],
            key=lambda c: _CAMERA_ORDER.get(c.get("role", "floor"), 9),
        )
        entered_visitors: set[str] = set()
        visitor_pool = StoreVisitorPool()
        billing_in_queue: set[str] = set()
        for camera in cameras:
            if args.camera and camera["camera_id"] != args.camera:
                continue
            print(f"Processing {store_id} / {camera['camera_id']} ...")
            evs = process_video(
                store_id,
                camera,
                model,
                sample_fps=args.sample_fps,
                max_frames=max_frames,
                entered_visitors=entered_visitors,
                visitor_pool=visitor_pool,
                billing_in_queue=billing_in_queue,
            )
            print(f"  -> {len(evs)} events (entered customers: {len(entered_visitors)})")
            all_events.extend(evs)

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        for e in all_events:
            f.write(json.dumps(e.model_dump(mode="json"), default=str) + "\n")
    print(f"Wrote {len(all_events)} events to {out_path}")

    if args.api_url:
        ingest_to_api(all_events, args.api_url)


if __name__ == "__main__":
    main()
