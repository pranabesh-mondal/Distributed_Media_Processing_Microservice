"""Tests for the Pillow image processor (hermetic; no infrastructure)."""

from pathlib import Path

import pytest
from PIL import Image

from app.services.processing.image_processor import (
    ImageProcessingError,
    process_image,
)


@pytest.fixture()
def source_png(tmp_path: Path) -> str:
    """Create a simple 100x80 PNG source image."""
    img = Image.new("RGB", (100, 80), (200, 100, 50))
    path = tmp_path / "source.png"
    img.save(path)
    return str(path)


def _result(path: Path):
    candidates = sorted(path.parent.glob(path.stem + ".*"))
    assert candidates, f"no output produced for {path}"
    return candidates[0]


def test_resize_single_dimension_preserves_aspect_ratio(
    source_png: str, tmp_path: Path
):
    out_base = str(tmp_path / "out")
    meta = process_image(
        source_png, out_base, [{"op": "resize", "params": {"width": 50}}]
    )
    out = Path(meta["output_path"])
    with Image.open(out) as img:
        assert img.width == 50
        # 100 -> 50 => height 80 -> 40
        assert img.height == 40


def test_compress_webp(source_png: str, tmp_path: Path):
    out_base = str(tmp_path / "out")
    meta = process_image(
        source_png, out_base, [{"op": "compress", "params": {"format": "webp"}}]
    )
    assert meta["format"] == "webp"
    with Image.open(meta["output_path"]) as img:
        assert img.format == "WEBP"


def test_watermark_requires_text(source_png: str, tmp_path: Path):
    out_base = str(tmp_path / "out")
    with pytest.raises(ImageProcessingError):
        process_image(source_png, out_base, [{"op": "watermark", "params": {}}])


def test_corrupt_image_fails_permanently(tmp_path: Path):
    bad = tmp_path / "bad.png"
    bad.write_bytes(b"this is not an image")
    with pytest.raises(ImageProcessingError):
        process_image(str(bad), str(tmp_path / "out"))


def test_transcode_rejected_for_images(source_png: str, tmp_path: Path):
    with pytest.raises(ImageProcessingError):
        process_image(
            source_png, str(tmp_path / "out"),
            [{"op": "transcode", "params": {"format": "mp4"}}],
        )