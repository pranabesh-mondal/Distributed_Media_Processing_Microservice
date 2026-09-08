"""Tests for the Prometheus metrics registry."""

from prometheus_client import generate_latest
from prometheus_client.registry import REGISTRY

from app.services import metrics


def _text() -> str:
    return generate_latest(REGISTRY).decode()


def _metric_value(name: str, labels: dict | None = None) -> str | None:
    """Parse the last value line for a metric from the exported text."""
    label_part = ""
    if labels:
        inner = ",".join(f'{k}="{v}"' for k, v in labels.items())
        label_part = "{" + inner + "}"
    needle_prefix = f"{name}{label_part} "
    value = None
    for line in _text().splitlines():
        if line.startswith(needle_prefix):
            value = line.split(needle_prefix, 1)[1]
    return value


def test_required_metrics_are_registered():
    expected = {
        "media_jobs_submitted_total",
        "media_jobs_completed_total",
        "media_jobs_failed_total",
        "media_upload_urls_issued_total",
        "media_s3_operations_total",
        "media_job_processing_seconds",
        "media_job_end_to_end_seconds",
        "media_jobs_in_progress",
    }
    exported = _text()
    assert all(name in exported for name in expected)


def test_counters_track_labels():
    metrics.JOBS_SUBMITTED.labels(media_type="image").inc(2)
    assert _metric_value(
        "media_jobs_submitted_total", {"media_type": "image"}
    ) in ("2", "2.0")
    # Different label is tracked independently: never incremented, so the
    # label appears as zero or is absent from the export entirely.
    video_value = _metric_value(
        "media_jobs_submitted_total", {"media_type": "video"}
    )
    assert video_value is None or video_value in ("0", "0.0")


def test_failed_counter_with_reason_label():
    metrics.JOBS_FAILED.labels(
        media_type="image", reason="dispatch_error"
    ).inc()
    value = _metric_value(
        "media_jobs_failed_total",
        {"media_type": "image", "reason": "dispatch_error"},
    )
    assert value is not None


def test_s3_operations_counter():
    metrics.S3_OPERATIONS.labels(
        operation="download", outcome="success"
    ).inc(3)
    value = _metric_value(
        "media_s3_operations_total",
        {"operation": "download", "outcome": "success"},
    )
    assert value in ("3", "3.0")


def test_gauges_inc_and_dec_balance():
    metrics.JOBS_IN_PROGRESS.labels(media_type="video").inc()
    metrics.JOBS_IN_PROGRESS.labels(media_type="video").dec()
    assert _metric_value(
        "media_jobs_in_progress", {"media_type": "video"}
    ) in ("0", "0.0")


def test_metrics_export_to_prometheus_text_format():
    """Scrape output contains our metric names (as served by /metrics)."""
    from app.main import app
    from fastapi.testclient import TestClient

    with TestClient(app) as client:
        resp = client.get("/metrics")
    assert resp.status_code == 200
    text = resp.text
    assert "media_jobs_submitted_total" in text
    assert "media_job_processing_seconds" in text