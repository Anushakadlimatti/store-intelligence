from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LAYOUT_PATH = ROOT / "store_layout.json"


def load_layout() -> dict:
    return json.loads(LAYOUT_PATH.read_text(encoding="utf-8"))


def get_store(store_id: str) -> dict | None:
    for s in load_layout()["stores"]:
        if s["store_id"] == store_id:
            return s
    return None


def get_camera(store_id: str, camera_id: str) -> dict | None:
    store = get_store(store_id)
    if not store:
        return None
    for cam in store["cameras"]:
        if cam["camera_id"] == camera_id:
            return cam
    return None


def get_video_path(store_id: str, camera_id: str) -> Path | None:
    cam = get_camera(store_id, camera_id)
    if not cam:
        return None
    path = ROOT / cam["video_path"]
    return path if path.exists() else None


def get_all_stores() -> list[dict]:
    return load_layout()["stores"]
