"""Health-check endpoint."""

from fastapi import APIRouter, Request

from app.config import get_settings

router = APIRouter(tags=["health"])


@router.get("/health")
def health_check(request: Request) -> dict:
    """Return basic service health and configuration status."""
    settings = get_settings()
    return {
        "status": "ok",
        "app": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "environment": settings.APP_ENV,
        "aws_configured": settings.s3_has_credentials,
        "s3_bucket": settings.S3_BUCKET_NAME,
    }
