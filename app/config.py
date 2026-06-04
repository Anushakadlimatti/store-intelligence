"""Shared port / API URL — override with env PORT or API_URL."""
from __future__ import annotations

import os

PORT = int(os.getenv("PORT", "8080"))
API_HOST = os.getenv("API_HOST", "127.0.0.1")


def api_base_url() -> str:
    return os.getenv("API_URL", f"http://{API_HOST}:{PORT}")
