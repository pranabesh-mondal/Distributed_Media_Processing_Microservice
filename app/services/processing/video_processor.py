"""FFmpeg-based video transformations (transcode, compress, resize, thumbnails).

Requires the FFmpeg system binary on PATH. When it is missing, a clear
:class:`FFmpegNotAvailableError` is raised so callers (and tests) can skip or
report the problem instead of crashing with an obscure error.

The public entry point is :func:`process_video`, which executes a list of
operations (dicts or :class:`app.models.media.OperationRequest`) in order.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import ffmpeg

from app.models.media import MediaOperation
from app.services.processing import split_operation
from app.services.retries import PermanentNetworkError

logger = logging.getLogger(__name__)


class VideoProcessingError(PermanentNetworkError):
    """Raised when a video cannot be processed (permanent — not retried)."""


class FFmpegNotAvailableError(VideoProcessingError):
    """Raised when the FFmpeg binary is not installed on this machine."""


# Target container formats for the transcode operation.
_VIDEO_FORMATS = ("mp4", "webm", "avi", "mov")

# Default encode settings per container.
_ENCODE_DEFAULTS: Dict[str, Dict[str, Any]] = {
    "mp4": {"vcodec": "libx264", "acodec": "aac"},
    "webm": {"vcodec": "libvpx-vp9", "acodec": "libopus"},
    "avi": {"vcodec": "mpeg4", "acodec": "libmp3lame"},
    "mov": {"vcodec": "libx264", "acodec": "aac"},
}

_CRF_RANGE = (0, 51)


def ffmpeg_available() -> bool:
    """Return True when the FFmpeg binary can be found on PATH."""
    return shutil.which("ffmpeg") is not None


def _require_ffmpeg() -> None:
    if not ffmpeg_available():
        raise FFmpegNotAvailableError(
            "FFmpeg binary not found on PATH. Install FFmpeg "
            "(https://ffmpeg.org) to process video."
        )


def _resize_scale(params: Dict[str, Any]) -> Tuple[str, str]:
    """Build the FFmpeg ``scale`` filter arguments from resize params."""
    width, height = params.get("width"), params.get("height")
    if not width and not height:
        raise VideoProcessingError("video resize requires 'width' and/or 'height'.")
    return (
        str(int(width)) if width else "-2",
        str(int(height)) if height else "-2",
    )


def _probe(path: Path) -> Dict[str, Any]:
    """Best-effort metadata (duration, dimensions) via ffprobe."""
    try:
        probe = ffmpeg.probe(str(path))
    except ffmpeg.Error:
        return {}
    info: Dict[str, Any] = {}
    duration = probe.get("format", {}).get("duration")
    if duration is not None:
        try:
            info["duration"] = round(float(duration), 2)
        except (TypeError, ValueError):
            pass
    for stream in probe.get("streams", []):
        if stream.get("codec_type") == "video":
            if stream.get("width"):
                info["width"] = stream["width"]
            if stream.get("height"):
                info["height"] = stream["height"]
            break
    return info


def _extract_thumbnail(
    input_path: str,
    output_path: Path,
    timestamp: float = 1.0,
    width: Optional[int] = None,
    height: Optional[int] = None,
) -> None:
    """Extract a single frame as a JPEG thumbnail."""
    try:
        stream = ffmpeg.input(str(input_path), ss=max(0.0, timestamp))
        if width or height:
            stream = stream.filter(
                "scale",
                str(int(width)) if width else "-2",
                str(int(height)) if height else "-2",
            )
        stream.output(str(output_path), vframes=1, **{"q:v": 2}).overwrite_output().run(quiet=True)
    except ffmpeg.Error as exc:
        stderr = (exc.stderr or b"").decode(errors="replace")[-1000:]
        raise VideoProcessingError(
            f"Thumbnail extraction failed at t={timestamp}s: {stderr}"
        ) from exc
    if not output_path.exists() or output_path.stat().st_size == 0:
        raise VideoProcessingError(
            f"Thumbnail extraction produced no frame at t={timestamp}s "
            "(timestamp may be beyond the video duration)."
        )


def process_video(
    input_path: str,
    output_base: str,
    operations: Optional[Sequence[Any]] = None,
) -> Dict[str, Any]:
    """Process a video, writing the result next to ``output_base``.

    The output file is ``{output_base}.{ext}`` where the extension matches the
    target container (default ``mp4``; changed by a ``transcode`` operation).
    Thumbnails are written as ``{output_base}_thumb{n}.jpg``.

    Args:
        input_path: Local path of the source video.
        output_base: Output path *without* extension.
        operations: Ordered list of operations (dicts or OperationRequest).

    Returns:
        Metadata dict with ``format``, ``output_path``, ``size_bytes``,
        ``duration``, ``width``, ``height`` and ``thumbnails`` (paths).

    Raises:
        FFmpegNotAvailableError: FFmpeg is not installed.
        VideoProcessingError: Invalid input, parameters, or FFmpeg failure.
    """
    ops = list(operations or [])
    _require_ffmpeg()

    scale: Optional[Tuple[str, str]] = None
    output_kwargs: Dict[str, Any] = {}
    output_format = "mp4"
    thumbnail_specs: List[Dict[str, Any]] = []
    needs_reencode = False

    for op in ops:
        name, params = split_operation(op)
        try:
            if name == MediaOperation.RESIZE.value:
                scale = _resize_scale(params)
            elif name in (MediaOperation.COMPRESS.value, MediaOperation.TRANSCODE.value):
                needs_reencode = True
                if name == MediaOperation.TRANSCODE.value:
                    requested = str(params.get("format", "mp4")).lower()
                    if requested not in _VIDEO_FORMATS:
                        raise VideoProcessingError(
                            f"Unsupported transcode format '{requested}'. "
                            f"Supported: {list(_VIDEO_FORMATS)}"
                        )
                    output_format = requested
                if params.get("crf") is not None:
                    crf = int(params["crf"])
                    if not _CRF_RANGE[0] <= crf <= _CRF_RANGE[1]:
                        raise VideoProcessingError(
                            f"crf must be between {_CRF_RANGE[0]} and {_CRF_RANGE[1]}."
                        )
                    output_kwargs["crf"] = crf
                if params.get("preset"):
                    output_kwargs["preset"] = str(params["preset"])
                if params.get("video_bitrate"):
                    output_kwargs["video_bitrate"] = str(params["video_bitrate"])
            elif name == MediaOperation.THUMBNAIL.value:
                thumbnail_specs.append({
                    "timestamp": float(params.get("timestamp", 1.0)),
                    "width": params.get("width"),
                    "height": params.get("height"),
                })
            elif name == MediaOperation.WATERMARK.value:
                raise VideoProcessingError(
                    "Watermarking is currently supported for images only."
                )
            else:
                raise VideoProcessingError(f"Unknown video operation '{name}'.")
        except VideoProcessingError:
            raise
        except (TypeError, ValueError) as exc:
            raise VideoProcessingError(
                f"Invalid parameters for operation '{name}': {exc}"
            ) from exc

    base = Path(output_base)
    base.parent.mkdir(parents=True, exist_ok=True)
    output_path = base.parent / f"{base.name}.{output_format}"

    try:
        stream = ffmpeg.input(str(input_path))
        if scale is not None:
            stream = stream.filter("scale", *scale)

        if needs_reencode or scale is not None:
            kwargs = dict(_ENCODE_DEFAULTS[output_format])
            kwargs.update(output_kwargs)
            if output_format in ("mp4", "mov"):
                kwargs["movflags"] = "faststart"  # web streaming friendly
            ffmpeg.output(stream, str(output_path), **kwargs).overwrite_output().run(quiet=True)
        else:
            # Nothing to re-encode: remux into the target container losslessly.
            ffmpeg.output(stream, str(output_path), c="copy").overwrite_output().run(quiet=True)
    except ffmpeg.Error as exc:
        stderr = (exc.stderr or b"").decode(errors="replace")[-2000:]
        raise VideoProcessingError(f"FFmpeg failed: {stderr}") from exc

    meta: Dict[str, Any] = {
        "format": output_format,
        "output_path": str(output_path),
        "size_bytes": output_path.stat().st_size,
        "thumbnails": [],
    }
    meta.update(_probe(output_path))

    for index, spec in enumerate(thumbnail_specs, start=1):
        thumb_path = base.parent / f"{base.name}_thumb{index}.jpg"
        _extract_thumbnail(input_path, thumb_path, **spec)
        meta["thumbnails"].append(str(thumb_path))

    logger.info("Processed video %s -> %s (%s)", input_path, output_path, meta)
    return meta