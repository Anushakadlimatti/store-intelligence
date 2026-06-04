from __future__ import annotations

from threading import RLock
from typing import Optional


class StoreVisitorPool:
    """
    Reuse visitor IDs from entry cameras for floor/billing cameras.
    Extra detections without a slot are treated as staff (no events).
    """

    def __init__(self, lock: Optional[RLock] = None) -> None:
        self.entered_order: list[str] = []
        self._active: set[str] = set()
        self._lock = lock

    def register_entry(self, visitor_id: str) -> None:
        if self._lock:
            with self._lock:
                self._register_entry(visitor_id)
        else:
            self._register_entry(visitor_id)

    def _register_entry(self, visitor_id: str) -> None:
        if visitor_id not in self.entered_order:
            self.entered_order.append(visitor_id)

    def borrow_for_new_track(self) -> str | None:
        if self._lock:
            with self._lock:
                return self._borrow_for_new_track()
        return self._borrow_for_new_track()

    def _borrow_for_new_track(self) -> str | None:
        for vid in self.entered_order:
            if vid not in self._active:
                self._active.add(vid)
                return vid
        return None

    def release(self, visitor_id: str) -> None:
        if self._lock:
            with self._lock:
                self._active.discard(visitor_id)
        else:
            self._active.discard(visitor_id)
