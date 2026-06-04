"""
Run detection on STORE_2 CCTV and push each event to the API immediately.

Modes (--mode):
  sequential    Process cameras one after another (entry -> floor -> billing).
  simultaneous  Process all cameras in parallel (all feeds at once).

Dashboard URLs:
  Sequential CCTV:    .../dashboard?store=STORE_2&cameras=sequential
  All cameras at once: .../dashboard?store=STORE_2&cameras=simultaneous

Terminal 1: bash start.sh
Terminal 2: python -m pipeline.live_detect --store STORE_2 --reset --mode sequential
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from threading import RLock

import httpx

from app.config import api_base_url
from pipeline.detect import _CAMERA_ORDER, load_yolo, process_video
from pipeline.layout import ROOT, get_store
from pipeline.visitor_pool import StoreVisitorPool

ROOT.joinpath("output").mkdir(parents=True, exist_ok=True)


def _clear_screen() -> None:
    if sys.platform == "win32":
        import os

        os.system("cls")
    else:
        print("\033[2J\033[H", end="")


def _dashboard_url(base: str, store_id: str, mode: str) -> str:
    cam = "simultaneous" if mode == "simultaneous" else "sequential"
    return f"{base}/dashboard?store={store_id}&cameras={cam}"


def _render_panel(
    store_id: str,
    run_mode: str,
    active_cameras: list[str],
    frame_n: int,
    events_sent: int,
    last_event: dict | None,
    live: dict,
    metrics: dict,
    api_base: str,
) -> None:
    areas = {a["area_id"]: a for a in live.get("areas", [])}
    ent = areas.get("ENTRANCE", {})
    floor = areas.get("MAIN_FLOOR", {})
    bill = areas.get("BILLING", {})
    last = ""
    if last_event:
        last = (
            f"{last_event.get('event_type')} {last_event.get('visitor_id')} "
            f"@ {last_event.get('zone_id', '-')}"
        )
    cams = ", ".join(active_cameras) if active_cameras else "(starting)"
    _clear_screen()
    print("=" * 60)
    print(f"  STORE INTELLIGENCE — LIVE PIPELINE ({store_id})")
    print("=" * 60)
    print(f"  Run mode:   {run_mode}")
    print(f"  API:        {api_base}")
    print(f"  Dashboard:  {_dashboard_url(api_base, store_id, run_mode)}")
    print(f"  Cameras:    {cams}")
    print(f"  Frames:     {frame_n}  |  Events sent: {events_sent}")
    print("-" * 60)
    print("  LIVE OCCUPANCY")
    print(f"    Entrance:   {ent.get('current', 0)}")
    print(f"    Main floor: {floor.get('current', 0)}")
    print(f"    Billing:    {bill.get('current', 0)}")
    print("-" * 60)
    print("  METRICS")
    print(f"    Unique visitors: {metrics.get('unique_visitors', 0)}")
    conv = metrics.get("conversion_rate", 0) or 0
    print(f"    Conversion:      {conv * 100:.1f}%")
    print(f"    Queue depth:     {metrics.get('queue_depth', 0)}")
    print("-" * 60)
    print(f"  Last event: {last or '(none yet)'}")
    print("=" * 60)
    sys.stdout.flush()


def _filter_cameras(store: dict, camera_id: str | None) -> list[dict]:
    cameras = sorted(
        store["cameras"],
        key=lambda c: _CAMERA_ORDER.get(c.get("role", "floor"), 9),
    )
    if camera_id:
        cameras = [c for c in cameras if c["camera_id"] == camera_id]
    return cameras


def _run_one_camera(
    *,
    store_id: str,
    camera: dict,
    model,
    sample_fps: float,
    max_frames: int | None,
    playback_realtime: bool,
    entered_visitors: set[str],
    visitor_pool: StoreVisitorPool,
    billing_in_queue: set[str],
    on_event,
    inference_lock: RLock | None,
    state_lock: RLock | None,
) -> list:
    return process_video(
        store_id,
        camera,
        model,
        sample_fps=sample_fps,
        max_frames=max_frames,
        entered_visitors=entered_visitors,
        visitor_pool=visitor_pool,
        billing_in_queue=billing_in_queue,
        on_event=on_event,
        playback_realtime=playback_realtime,
        inference_lock=inference_lock,
        state_lock=state_lock,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Live detection + API ingest")
    parser.add_argument("--store", default="STORE_2")
    parser.add_argument(
        "--mode",
        choices=("sequential", "simultaneous"),
        default="sequential",
        help="sequential: cameras in order; simultaneous: all cameras in parallel",
    )
    parser.add_argument("--api-url", default=None)
    parser.add_argument("--sample-fps", type=float, default=2.0)
    parser.add_argument("--reset", action="store_true")
    parser.add_argument("--fast", action="store_true")
    parser.add_argument("--max-frames", type=int, default=None)
    parser.add_argument("--camera", default=None)
    parser.add_argument("--no-playback", action="store_true")
    args = parser.parse_args()

    store = get_store(args.store)
    if not store:
        print(f"Unknown store {args.store}")
        sys.exit(1)

    base = (args.api_url or api_base_url()).rstrip("/")
    max_frames = 90 if args.fast else args.max_frames
    cameras = _filter_cameras(store, args.camera)

    state = {
        "events_sent": 0,
        "last_event": None,
        "frame_n": 0,
        "active_cameras": [],
        "live": {"areas": []},
        "metrics": {"unique_visitors": 0, "conversion_rate": 0, "queue_depth": 0},
    }
    ui_lock = threading.Lock()
    ingest_lock = threading.Lock()

    with httpx.Client(timeout=60.0) as client:
        try:
            client.get(f"{base}/health").raise_for_status()
        except Exception as e:
            print(f"Cannot reach API at {base}: {e}")
            print("Start the API first: bash start.sh")
            sys.exit(1)

        if args.reset:
            print("Reset:", client.post(f"{base}/admin/reset-live/{args.store}").json())

        def on_event(ev, camera_id: str) -> None:
            payload = ev.model_dump(mode="json")
            with ingest_lock:
                client.post(f"{base}/events/ingest", json={"events": [payload]}).raise_for_status()
                state["events_sent"] += 1
                state["last_event"] = payload
                if camera_id not in state["active_cameras"]:
                    state["active_cameras"].append(camera_id)
                try:
                    state["live"] = client.get(f"{base}/stores/{args.store}/live").json()
                    state["metrics"] = client.get(f"{base}/stores/{args.store}/metrics").json()
                except Exception:
                    pass
            with ui_lock:
                _render_panel(
                    args.store,
                    args.mode,
                    list(state["active_cameras"]),
                    state["frame_n"],
                    state["events_sent"],
                    state["last_event"],
                    state["live"],
                    state["metrics"],
                    base,
                )

        dash = _dashboard_url(base, args.store, args.mode)
        print(f"Mode: {args.mode}")
        print(f"Dashboard ({args.mode} CCTV): {dash}\n")
        time.sleep(1)

        model = load_yolo()
        entered_visitors: set[str] = set()
        billing_in_queue: set[str] = set()
        inference_lock = RLock() if args.mode == "simultaneous" else None
        state_lock = RLock() if args.mode == "simultaneous" else None
        visitor_pool = StoreVisitorPool(lock=state_lock)
        all_events = []

        def make_callback(cam_id: str):
            def _cb(ev):
                on_event(ev, cam_id)

            return _cb

        if args.mode == "sequential":
            for camera in cameras:
                cam_id = camera["camera_id"]
                print(f"Starting {cam_id} ({camera['role']})...")
                state["active_cameras"] = [cam_id]
                evs = _run_one_camera(
                    store_id=args.store,
                    camera=camera,
                    model=model,
                    sample_fps=args.sample_fps,
                    max_frames=max_frames,
                    playback_realtime=not args.no_playback,
                    entered_visitors=entered_visitors,
                    visitor_pool=visitor_pool,
                    billing_in_queue=billing_in_queue,
                    on_event=make_callback(cam_id),
                    inference_lock=None,
                    state_lock=None,
                )
                state["frame_n"] += max_frames or len(evs)
                all_events.extend(evs)
                print(f"  Done {cam_id}: {len(evs)} events")
        else:
            print(f"Starting {len(cameras)} cameras in parallel...")
            with ThreadPoolExecutor(max_workers=len(cameras)) as pool:
                futures = {
                    pool.submit(
                        _run_one_camera,
                        store_id=args.store,
                        camera=camera,
                        model=model,
                        sample_fps=args.sample_fps,
                        max_frames=max_frames,
                        playback_realtime=not args.no_playback,
                        entered_visitors=entered_visitors,
                        visitor_pool=visitor_pool,
                        billing_in_queue=billing_in_queue,
                        on_event=make_callback(camera["camera_id"]),
                        inference_lock=inference_lock,
                        state_lock=state_lock,
                    ): camera["camera_id"]
                    for camera in cameras
                }
                for fut in as_completed(futures):
                    cam_id = futures[fut]
                    try:
                        evs = fut.result()
                        all_events.extend(evs)
                        print(f"  Done {cam_id}: {len(evs)} events")
                    except Exception as exc:
                        print(f"  Failed {cam_id}: {exc}")

        out = ROOT / "output" / f"{args.store.lower()}_{args.mode}_live.jsonl"
        with open(out, "w", encoding="utf-8") as f:
            for e in all_events:
                f.write(json.dumps(e.model_dump(mode="json"), default=str) + "\n")

        _render_panel(
            args.store,
            args.mode,
            [c["camera_id"] for c in cameras],
            state["frame_n"],
            state["events_sent"],
            state["last_event"],
            state["live"],
            state["metrics"],
            base,
        )
        print(f"\nFinished ({args.mode}). {len(all_events)} events -> {out}")
        print(f"Dashboard: {dash}")


if __name__ == "__main__":
    main()
