"""
Transcript fetching utilities with error handling, caching, and diagnostics.
"""

import logging
import re
from typing import Optional, Tuple
from django.core.cache import cache

logger = logging.getLogger(__name__)


class TranscriptFetchError(Exception):
    """Base exception for transcript fetch errors."""

    def __init__(self, error_code: str, message: str, http_status: int = 502):
        self.error_code = error_code
        self.message = message
        self.http_status = http_status
        super().__init__(message)


class YouTubeBlockedError(TranscriptFetchError):
    """YouTube is blocking the request (403, 429, captcha)."""

    def __init__(self, details: str = ""):
        super().__init__(
            error_code="youtube_blocked",
            message=f"YouTube blocked access. Try MP3 upload or paste transcript. ({details})",
            http_status=502,
        )


class NoTranscriptError(TranscriptFetchError):
    """Video has no captions/transcript."""

    def __init__(self):
        super().__init__(
            error_code="no_transcript",
            message="Video has no captions available. Try MP3 upload or paste transcript.",
            http_status=502,
        )


class DownloadError(TranscriptFetchError):
    """Audio download failed."""

    def __init__(self, details: str = ""):
        super().__init__(
            error_code="download_failed",
            message=f"Could not download audio. Try MP3 upload. ({details})",
            http_status=502,
        )


def extract_video_id(youtube_url: str) -> str:
    """Extract video ID from YouTube URL."""
    patterns = [
        r"(?:youtube\.com\/watch\?v=|youtu\.be\/)([0-9A-Za-z_-]{11})",
        r"youtube\.com\/shorts\/([0-9A-Za-z_-]{11})",
        r"youtube\.com\/embed\/([0-9A-Za-z_-]{11})",
    ]
    for pattern in patterns:
        match = re.search(pattern, youtube_url)
        if match:
            return match.group(1)
    raise ValueError(f"Could not extract video ID from {youtube_url}")


def get_transcript_with_diagnostics(
    youtube_url: str, get_transcript_func, timeout: int = 3600
) -> Tuple[Optional[str], Optional[TranscriptFetchError]]:
    """
    Fetch transcript with caching and error handling.

    Args:
        youtube_url: Full YouTube URL
        get_transcript_func: Callable that fetches transcript (returns str or None)
        timeout: Cache timeout in seconds

    Returns:
        (transcript_text, error)
        - If successful: (text, None)
        - If failed: (None, TranscriptFetchError)
    """
    try:
        video_id = extract_video_id(youtube_url)
    except ValueError as e:
        return None, YouTubeBlockedError(f"Invalid URL: {str(e)}")

    cache_key = f"transcript:video:{video_id}"

    # Check if transcript is cached
    cached = cache.get(cache_key)
    if cached is not None:
        if isinstance(cached, str) and cached:
            # Return cached transcript
            logger.info(f"Returning cached transcript for video {video_id}")
            return cached, None
        elif isinstance(cached, dict) and cached.get("is_error"):
            # Return cached error (failure cache to reduce retries)
            logger.info(f"Returning cached error for video {video_id}")
            error_data = cached.get("error", {})
            return None, TranscriptFetchError(
                error_code=error_data.get("error_code", "unknown"),
                message=error_data.get("message", "Unknown error"),
                http_status=error_data.get("http_status", 502),
            )

    # Fetch transcript using provided function
    try:
        logger.info(f"Fetching transcript for video {video_id}")
        transcript_text = get_transcript_func(youtube_url)

        if not transcript_text:
            error = NoTranscriptError()
            # Cache the failure for 1 hour to reduce retries
            cache.set(
                cache_key,
                {
                    "is_error": True,
                    "error": {
                        "error_code": error.error_code,
                        "message": error.message,
                        "http_status": error.http_status,
                    },
                },
                timeout=3600,
            )
            logger.warning(f"No transcript for video {video_id}")
            return None, error

        # Cache success
        cache.set(cache_key, transcript_text, timeout=timeout)
        logger.info(f"Successfully cached transcript for video {video_id}")
        return transcript_text, None

    except Exception as e:
        # Detect the specific error type
        error_str = str(e).lower()

        if "403" in error_str or "forbidden" in error_str:
            error = YouTubeBlockedError("HTTP 403 Forbidden")
        elif "429" in error_str or "too many requests" in error_str:
            error = YouTubeBlockedError("HTTP 429 Too Many Requests")
        elif "captcha" in error_str:
            error = YouTubeBlockedError("CAPTCHA required")
        elif "not available" in error_str or "no captions" in error_str:
            error = NoTranscriptError()
        else:
            error = YouTubeBlockedError(f"Error: {str(e)[:50]}")

        # Cache the error for 10 minutes to reduce retries on rate limits
        cache.set(
            cache_key,
            {
                "is_error": True,
                "error": {
                    "error_code": error.error_code,
                    "message": error.message,
                    "http_status": error.http_status,
                },
            },
            timeout=600,
        )

        logger.exception(
            f"Transcript fetch failed for {video_id}: {type(e).__name__}: {str(e)}"
        )
        return None, error
