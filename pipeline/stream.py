from __future__ import annotations

import time

import cv2

from pipeline.layout import get_video_path


def generate_mjpeg_stream(store_id: str, camera_id: str, fps: float = 8.0):
    path = get_video_path(store_id, camera_id)
    if not path:
        return

    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        return

    interval = 1.0 / fps
    try:
        while True:
            ret, frame = cap.read()
            if not ret:
                cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                continue
            _, buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 75])
            yield (
                b"--frame\r\n"
                b"Content-Type: image/jpeg\r\n\r\n" + buf.tobytes() + b"\r\n"
            )
            time.sleep(interval)
    finally:
        cap.release()
