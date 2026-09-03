"""Media processing layer (Pillow for images, FFmpeg for videos)."""

from __future__ import annotations

from typing import Any, Dict, Tuple

from app.services.retries import PermanentNetworkError


class UnsupportedMediaError(PermanentNetworkError):
    """Raised when a job's media type cannot be routed to a processor."""


# Canonical format name (as returned by the processors) -> file extension.
FORMAT_EXTENSIONS: Dict[str, str] = {
    "jpeg": "jpg",
    "jpg": "jpg",
    "png": "png",
    "webp": "webp",
    "bmp": "bmp",
    "gif": "gif",
    "mp4": "mp4",
    "webm": "webm",
    "avi": "avi",
    "mov": "mov",
}


def split_operation(op: Any) -> Tuple[str, Dict[str, Any]]:
    """Normalise an operation given either as a dict or a pydantic model.

    Returns:
        ``(operation_name, params_dict)``
    """
    if isinstance(op, dict):
        return str(op.get("op")), dict(op.get("params") or {})
    return str(getattr(op, "op", "")), dict(getattr(op, "params", {}) or {})