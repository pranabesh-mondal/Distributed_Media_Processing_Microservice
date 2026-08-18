"""Application configuration.

Reads configuration values from environment variables and an optional `.env`
file using pydantic-settings. All settings are cached so the same object is
reused across the application.
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Typed application settings loaded from the environment / .env file."""

    # ---- Application -------------------------------------------------------
    APP_NAME: str = "Distributed Media Processing Microservice"
    APP_VERSION: str = "0.1.0"
    APP_DESCRIPTION: str = (
        "Event-driven backend microservice for heavy, asynchronous media processing."
    )
    APP_ENV: str = "development"
    APP_HOST: str = "0.0.0.0"
    APP_PORT: int = 8000
    DEBUG: bool = False

    # ---- AWS / S3 ----------------------------------------------------------
    AWS_ACCESS_KEY_ID: str = ""
    AWS_SECRET_ACCESS_KEY: str = ""
    AWS_REGION: str = "us-east-1"
    S3_BUCKET_NAME: str = ""
    CLOUDFRONT_DOMAIN: str = ""
    # How long pre-signed S3 URLs remain valid, in seconds.
    S3_PRESIGNED_URL_EXPIRY: int = 3600
    # Optional custom S3 endpoint (e.g. LocalStack for local development).
    S3_ENDPOINT_URL: str = ""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    @property
    def s3_has_credentials(self) -> bool:
        """Whether explicit AWS credentials are configured.

        If false, boto3 falls back to the standard AWS credential chain
        (environment variables, shared credentials file, IAM roles, etc.).
        """
        return bool(self.AWS_ACCESS_KEY_ID and self.AWS_SECRET_ACCESS_KEY)


@lru_cache
def get_settings() -> Settings:
    """Return the application settings, cached after first load."""
    return Settings()
