"""Tests for the media processing request/response schemas."""

from app.models.job import Job, JobCreateRequest, JobStatus
from app.models.media import MediaOperation, MediaType, OperationRequest


def test_operation_request_defaults():
    op = OperationRequest(op=MediaOperation.RESIZE, params={"width": 100})
    assert op.params == {"width": 100}


def test_job_create_request_round_trip():
    req = JobCreateRequest(
        media_type=MediaType.IMAGE,
        source_key="uploads/images/x.png",
        operations=[
            OperationRequest(op=MediaOperation.RESIZE, params={"width": 50}),
        ],
    )
    data = req.model_dump()
    assert data["media_type"] == "image"
    assert data["operations"][0]["op"] == "resize"


def test_job_status_values():
    assert JobStatus.PENDING.value == "pending"
    assert JobStatus.COMPLETED.value == "completed"
    assert JobStatus.FAILED.value == "failed"


def test_job_has_cdn_url_fields():
    job = Job(job_id="00000000-0000-0000-0000-000000000000")
    assert job.result_url is None
    assert job.thumbnail_urls is None