from app.config import Settings


MIN_PRESIGNED_EXPIRY_SECONDS = 60


def clamp_presigned_expiry(settings: Settings, requested_seconds: int | None = None) -> int:
    """Return one policy-compliant expiry for every presigned URL path."""

    requested = (
        settings.presigned_url_default_expiry_seconds
        if requested_seconds is None
        else requested_seconds
    )
    if requested < MIN_PRESIGNED_EXPIRY_SECONDS:
        raise ValueError(
            f"presigned URL expiry must be at least {MIN_PRESIGNED_EXPIRY_SECONDS} seconds"
        )
    return min(requested, settings.presigned_url_max_expiry_seconds)
