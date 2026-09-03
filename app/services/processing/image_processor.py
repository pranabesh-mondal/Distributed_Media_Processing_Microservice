"""Pillow-based image transformations (resize, compress, watermark, thumbnail).

All functions are CPU-bound and broker-independent so they can be unit-tested
without any infrastructure. Processing errors raise
:class:`ImageProcessingError`, which derives from ``PermanentNetworkError``:
a corrupt input will never succeed on retry, so Celery must not requeue it.

The public entry point is :func:`process_image`, which executes a list of
operations (dicts or :class:`app.models.media.OperationRequest`) in order.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, Optional, Sequence, Tuple

from PIL import Image, ImageDraw, ImageFont, UnidentifiedImageError

from app.models.media import MediaOperation
from app.services.processing import split_operation
from app.services.retries import PermanentNetworkError

logger = logging.getLogger(__name__)


class ImageProcessingError(PermanentNetworkError):
    """Raised when an image cannot be processed (permanent — not retried)."""


# Pillow format name -> (file extension, MIME type).
FORMAT_INFO: Dict[str, Tuple[str, str]] = {
    "JPEG": ("jpg", "image/jpeg"),
    "PNG": ("png", "image/png"),
    "WEBP": ("webp", "image/webp"),
    "BMP": ("bmp", "image/bmp"),
    "GIF": ("gif", "image/gif"),
}

# User-facing format names -> Pillow format names (for the compress op).
_USER_FORMATS = {
    "jpeg": "JPEG",
    "jpg": "JPEG",
    "png": "PNG",
    "webp": "WEBP",
    "bmp": "BMP",
    "gif": "GIF",
}

_POSITIONS = ("top-left", "top-right", "bottom-left", "bottom-right", "center")

_DEFAULT_QUALITY = 85
_DEFAULT_THUMBNAIL = (320, 240)  # README: THUMBNAIL_SIZE=(320, 240)


def _load_font(size: int):
    """Load a scalable TTF font, falling back to Pillow's default bitmap font."""
    for name in ("arial.ttf", "DejaVuSans.ttf", "Helvetica.ttf"):
        try:
            return ImageFont.truetype(name, size=size)
        except OSError:
            continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # older Pillow without the size argument
        return ImageFont.load_default()


def _target_format(operations: Sequence[Any], source_format: str) -> str:
    """Return the Pillow format the pipeline will save as.

    The final format is the last ``compress`` operation's ``format`` param,
    defaulting to the source format (or PNG when the source format is not
    supported for saving, e.g. SVG).
    """
    fmt = source_format if source_format in FORMAT_INFO else "PNG"
    for op in operations:
        name, params = split_operation(op)
        if name == MediaOperation.COMPRESS.value:
            requested = params.get("format")
            if requested:
                pillow_format = _USER_FORMATS.get(str(requested).lower())
                if not pillow_format:
                    raise ImageProcessingError(
                        f"Unsupported compress format '{requested}'. "
                        f"Supported: {sorted(_USER_FORMATS)}"
                    )
                fmt = pillow_format
    return fmt


def _resize(img: Image.Image, params: Dict[str, Any]) -> Image.Image:
    """Resize keeping aspect ratio when only one dimension is given.

    With both ``width`` and ``height`` the image is resized to exactly that
    size (which may distort it). Upscaling is disabled unless
    ``allow_upscale`` is true.
    """
    width = params.get("width")
    height = params.get("height")
    if not width and not height:
        raise ImageProcessingError("resize requires 'width' and/or 'height'.")

    base_w, base_h = img.size
    if width and height:
        new_size = (int(width), int(height))
    elif width:
        new_size = (int(width), max(1, round(base_h * int(width) / base_w)))
    else:
        new_size = (max(1, round(base_w * int(height) / base_h)), int(height))

    if not params.get("allow_upscale", False) and new_size[0] >= base_w and new_size[1] >= base_h:
        return img
    return img.resize(new_size, Image.Resampling.LANCZOS)


def _watermark(img: Image.Image, params: Dict[str, Any]) -> Image.Image:
    """Overlay a semi-transparent text watermark on the image."""
    text = params.get("text")
    if not text:
        raise ImageProcessingError("watermark requires 'text'.")

    opacity = max(0, min(100, int(params.get("opacity", 50))))
    font_size = int(params.get("font_size", max(12, img.size[1] // 20)))
    position = str(params.get("position", "bottom-right"))
    if position not in _POSITIONS:
        raise ImageProcessingError(
            f"Invalid watermark position '{position}'. Supported: {list(_POSITIONS)}"
        )
    margin = int(params.get("margin", 10))

    base = img.convert("RGBA")
    overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    font = _load_font(font_size)
    bbox = draw.textbbox((0, 0), str(text), font=font)
    text_size = (bbox[2] - bbox[0], bbox[3] - bbox[1])

    x, y = {
        "top-left": (margin, margin),
        "top-right": (base.width - text_size[0] - margin, margin),
        "bottom-left": (margin, base.height - text_size[1] - margin),
        "bottom-right": (base.width - text_size[0] - margin, base.height - text_size[1] - margin),
        "center": ((base.width - text_size[0]) // 2, (base.height - text_size[1]) // 2),
    }[position]

    alpha = int(255 * opacity / 100)
    draw.text((x, y), str(text), font=font, fill=(255, 255, 255, alpha))
    return Image.alpha_composite(base, overlay)


def _thumbnail(img: Image.Image, params: Dict[str, Any]) -> Image.Image:
    """Create a thumbnail that fits within width x height, aspect preserved."""
    width = int(params.get("width", _DEFAULT_THUMBNAIL[0]))
    height = int(params.get("height", _DEFAULT_THUMBNAIL[1]))
    if width <= 0 or height <= 0:
        raise ImageProcessingError("thumbnail 'width' and 'height' must be positive.")
    thumb = img.copy()
    thumb.thumbnail((width, height), Image.Resampling.LANCZOS)
    return thumb


def process_image(
    input_path: str,
    output_base: str,
    operations: Optional[Sequence[Any]] = None,
) -> Dict[str, Any]:
    """Process an image, writing the result next to ``output_base``.

    The output file is ``{output_base}.{ext}`` — the extension always matches
    the final format so callers can upload/rename the file safely.

    Args:
        input_path: Local path of the source image.
        output_base: Output path *without* extension.
        operations: Ordered list of operations (dicts or OperationRequest).

    Returns:
        Metadata dict with ``format``, ``width``, ``height``, ``size_bytes``
        and ``output_path``.

    Raises:
        ImageProcessingError: If the input is unreadable or any operation
            is invalid.
    """
    ops = list(operations or [])
    try:
        img = Image.open(input_path)
        img.load()
    except (UnidentifiedImageError, OSError) as exc:
        raise ImageProcessingError(f"Cannot open image '{input_path}': {exc}") from exc

    source_format = (img.format or "").upper()
    save_format = _target_format(ops, source_format)
    quality = _DEFAULT_QUALITY

    for op in ops:
        name, params = split_operation(op)
        try:
            if name == MediaOperation.RESIZE.value:
                img = _resize(img, params)
            elif name == MediaOperation.COMPRESS.value:
                quality = int(params.get("quality", _DEFAULT_QUALITY))
            elif name == MediaOperation.WATERMARK.value:
                img = _watermark(img, params)
            elif name == MediaOperation.THUMBNAIL.value:
                img = _thumbnail(img, params)
            elif name == MediaOperation.TRANSCODE.value:
                raise ImageProcessingError("'transcode' applies to video only.")
            else:
                raise ImageProcessingError(f"Unknown image operation '{name}'.")
        except ImageProcessingError:
            raise
        except Exception as exc:  # Pillow raises a wide variety of errors
            raise ImageProcessingError(f"Operation '{name}' failed: {exc}") from exc

    # JPEG has no alpha channel: flatten before saving.
    if save_format == "JPEG" and img.mode not in ("RGB", "L"):
        img = img.convert("RGB")

    extension = FORMAT_INFO[save_format][0]
    output_path = Path(f"{output_base}.{extension}")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    save_kwargs: Dict[str, Any] = {}
    if save_format in ("JPEG", "WEBP"):
        save_kwargs["quality"] = max(1, min(95, quality))
    if save_format in ("PNG", "JPEG"):
        save_kwargs["optimize"] = True

    img.save(output_path, format=save_format, **save_kwargs)

    meta: Dict[str, Any] = {
        "format": save_format.lower(),
        "width": img.width,
        "height": img.height,
        "size_bytes": output_path.stat().st_size,
        "output_path": str(output_path),
    }
    logger.info("Processed image %s -> %s (%s)", input_path, output_path, meta)
    return meta