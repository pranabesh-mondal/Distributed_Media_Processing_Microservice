"""Prometheus metrics for the media processing microservice.

Central registry with all metrics exported at ``GET /metrics``:

* Counters: jobs submitted / completed / failed, upload URLs issued, S3 ops.
* Histograms: worker processing duration and job end-to-end latency.
* Gauges: jobs currently in progress.

Metric names follow Prometheus conventions (``media_*`` prefix).
"""

from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

# ---- Counters -------------------------------------------------------------

JOBS_SUBMITTED = Counter(
    "media_jobs_submitted_total",
    "Total number of processing jobs submitted.",
    ["media_type"],
)

JOBS_COMPLETED = Counter(
    "media_jobs_completed_total",
    "Total number of jobs processed successfully.",
    ["media_type"],
)

JOBS_FAILED = Counter(
    "media_jobs_failed_total",
    "Total number of jobs that failed permanently.",
    ["media_type", "reason"],
)

UPLOAD_URLS_ISSUED = Counter(
    "media_upload_urls_issued_total",
    "Total number of pre-signed upload URLs issued.",
    ["media_type"],
)

S3_OPERATIONS = Counter(
    "media_s3_operations_total",
    "S3 operations performed by the service.",
    ["operation", "outcome"],
)

# ---- Histograms -----------------------------------------------------------

JOB_PROCESSING_SECONDS = Histogram(
    "media_job_processing_seconds",
    "Time a worker spends processing a job (download/process/upload).",
    ["media_type"],
    buckets=(0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60, 120, 300, 600),
)

JOB_END_TO_END_SECONDS = Histogram(
    "media_job_end_to_end_seconds",
    "Time from job submission to completion (queue wait included).",
    ["media_type"],
    buckets=(0.5, 1, 2.5, 5, 10, 30, 60, 120, 300, 600, 900),
)

# ---- Gauges ---------------------------------------------------------------

JOBS_IN_PROGRESS = Gauge(
    "media_jobs_in_progress",
    "Number of jobs currently being processed by workers.",
    ["media_type"],
)
