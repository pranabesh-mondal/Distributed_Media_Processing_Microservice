"""Tests for app.utils.helpers."""

from app.utils.helpers import (
    build_cdn_url,
    generate_object_key,
    infer_content_type,
    sanitize_filename,
)


def test_generate_object_key_is_unique_and_sanitized():
    key_a = generate_object_key("uploads/images", "My Photo.JPG")
    key_b = generate_object_key("uploads/images", "My Photo.JPG")
    assert key_a.startswith("uploads/images/")
    assert key_a.endswith(".jpg")
    assert key_a != key_b  # uuid-based uniqueness


def test_generate_object_key_strips_unsafe_characters():
    key = generate_object_key("uploads", "weird@@name%.txt")
    assert key.endswith(".txt")
    assert "%" not in key


def test_sanitize_filename():
    assert sanitize_filename("a b/c@d.txt") == "a_b_c_d.txt"
    assert sanitize_filename("._hidden_.") == "hidden"


def test_infer_content_type_known_and_unknown():
    assert infer_content_type("photo.JPG") == "image/jpeg"
    assert infer_content_type("clip.mp4") == "video/mp4"
    assert infer_content_type("file.unknown") is None


def test_build_cdn_url():
    assert (build_cdn_url("processed/a.jpg", "cdn.example.com")
            == "https://cdn.example.com/processed/a.jpg")
    # Leading/trailing slashes are normalized.
    assert (build_cdn_url("/processed/a.jpg", "cdn.example.com/")
            == "https://cdn.example.com/processed/a.jpg")
    assert build_cdn_url("processed/a.jpg", "") is None
