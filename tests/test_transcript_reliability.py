"""
Tests for robust transcript fetching with error handling and caching.
"""

import pytest
from django.test import TestCase, Client
from django.contrib.auth.models import User
from unittest.mock import patch, MagicMock
from Backend.note_generator.views import generate_note
from Backend.note_generator.transcript_utils import (
    TranscriptFetchError,
    YouTubeBlockedError,
    NoTranscriptError,
    get_transcript_with_diagnostics,
    extract_video_id,
)
import json


@pytest.fixture
def test_user():
    """Create a test user."""
    return User.objects.create_user(username="testuser", password="testpass123")


@pytest.fixture
def authenticated_client(test_user):
    """Create an authenticated client."""
    client = Client()
    client.login(username="testuser", password="testpass123")
    return client


class TestExtractVideoId(TestCase):
    """Test video ID extraction."""

    def test_extract_from_watch_url(self):
        url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
        assert extract_video_id(url) == "dQw4w9WgXcQ"

    def test_extract_from_youtu_be(self):
        url = "https://youtu.be/dQw4w9WgXcQ"
        assert extract_video_id(url) == "dQw4w9WgXcQ"

    def test_extract_from_shorts(self):
        url = "https://www.youtube.com/shorts/dQw4w9WgXcQ"
        assert extract_video_id(url) == "dQw4w9WgXcQ"

    def test_extract_invalid_url(self):
        with pytest.raises(ValueError):
            extract_video_id("https://example.com/invalid")


@pytest.mark.django_db
class TestTranscriptErrorHandling(TestCase):
    """Test error handling in transcript fetching."""

    def test_youtube_blocked_error_403(self):
        error = YouTubeBlockedError("HTTP 403 Forbidden")
        assert error.error_code == "youtube_blocked"
        assert "403" in error.message
        assert error.http_status == 502

    def test_youtube_blocked_error_429(self):
        error = YouTubeBlockedError("HTTP 429 Too Many Requests")
        assert error.error_code == "youtube_blocked"
        assert "429" in error.message or "blocked" in error.message.lower()
        assert error.http_status == 502

    def test_no_transcript_error(self):
        error = NoTranscriptError()
        assert error.error_code == "no_transcript"
        assert error.http_status == 502


@pytest.mark.django_db
class TestGenerateNoteEndpoint(TestCase):
    """Test the /generate-notes endpoint with various error scenarios."""

    def setUp(self):
        self.user = User.objects.create_user(
            username="testuser", password="testpass123"
        )
        self.client = Client()
        self.client.login(username="testuser", password="testpass123")

    def test_missing_youtube_link(self):
        response = self.client.post(
            "/generate-notes",
            data=json.dumps({"link": ""}),
            content_type="application/json",
        )
        assert response.status_code == 400
        data = response.json()
        assert "error_code" in data

    def test_invalid_url_format(self):
        response = self.client.post(
            "/generate-notes",
            data=json.dumps({"link": "not-a-youtube-link"}),
            content_type="application/json",
        )
        assert response.status_code == 400
        data = response.json()
        assert data["error_code"] == "invalid_url"

    @patch("note_generator.views.get_transcript")
    @patch("note_generator.views.yt_title")
    def test_transcript_fetch_403_blocked(self, mock_yt_title, mock_get_transcript):
        """Simulate YouTube blocking with HTTP 403."""
        mock_yt_title.return_value = "Test Video"
        mock_get_transcript.side_effect = Exception("HTTP 403: Forbidden")

        response = self.client.post(
            "/generate-notes",
            data=json.dumps({"link": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"}),
            content_type="application/json",
        )

        assert response.status_code == 502
        data = response.json()
        assert data["error_code"] == "youtube_blocked"
        assert "Try MP3 upload" in data["message"]

    @patch("note_generator.views.get_transcript")
    @patch("note_generator.views.yt_title")
    def test_transcript_fetch_429_rate_limited(
        self, mock_yt_title, mock_get_transcript
    ):
        """Simulate rate limiting with HTTP 429."""
        mock_yt_title.return_value = "Test Video"
        mock_get_transcript.side_effect = Exception("HTTP 429: Too Many Requests")

        response = self.client.post(
            "/generate-notes",
            data=json.dumps({"link": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"}),
            content_type="application/json",
        )

        assert response.status_code == 502
        data = response.json()
        assert data["error_code"] == "youtube_blocked"

    @patch("note_generator.views.get_transcript")
    @patch("note_generator.views.yt_title")
    def test_transcript_none_no_captions(self, mock_yt_title, mock_get_transcript):
        """Simulate video with no captions."""
        mock_yt_title.return_value = "Test Video"
        mock_get_transcript.return_value = None

        response = self.client.post(
            "/generate-notes",
            data=json.dumps({"link": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"}),
            content_type="application/json",
        )

        assert response.status_code == 502
        data = response.json()
        assert data["error_code"] == "no_transcript"

    @patch("note_generator.views.generate_blog_from_transcription")
    @patch("note_generator.views.get_transcript")
    @patch("note_generator.views.yt_title")
    def test_generation_failure_503(
        self, mock_yt_title, mock_get_transcript, mock_generate_blog
    ):
        """Simulate note generation service failure (503)."""
        mock_yt_title.return_value = "Test Video"
        mock_get_transcript.return_value = "Test transcript content"
        mock_generate_blog.side_effect = Exception("OpenAI service unavailable")

        response = self.client.post(
            "/generate-notes",
            data=json.dumps({"link": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"}),
            content_type="application/json",
        )

        assert response.status_code == 503
        data = response.json()
        assert data["error_code"] == "generation_failed"

    @patch("note_generator.views.generate_blog_from_transcription")
    @patch("note_generator.views.get_transcript")
    @patch("note_generator.views.yt_title")
    def test_successful_note_generation(
        self, mock_yt_title, mock_get_transcript, mock_generate_blog
    ):
        """Test successful note generation."""
        mock_yt_title.return_value = "Test Video Title"
        mock_get_transcript.return_value = "Test transcript with content"
        mock_generate_blog.return_value = "# Generated Notes\n\nTest content"

        response = self.client.post(
            "/generate-notes",
            data=json.dumps({"link": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"}),
            content_type="application/json",
        )

        assert response.status_code == 200
        data = response.json()
        assert "content" in data
        assert "Generated Notes" in data["content"]

    def test_not_authenticated(self):
        """Test that unauthenticated requests are redirected."""
        self.client.logout()
        response = self.client.post(
            "/generate-notes",
            data=json.dumps({"link": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"}),
            content_type="application/json",
        )
        # Django redirects to login for @login_required
        assert response.status_code in [302, 403]


@pytest.mark.django_db
class TestTranscriptCaching(TestCase):
    """Test transcript caching functionality."""

    @patch("note_generator.views.get_transcript")
    def test_transcript_cached_on_success(self, mock_get_transcript):
        """Test that successful transcripts are cached."""
        from django.core.cache import cache

        mock_get_transcript.return_value = "Test transcript"

        # Clear cache first
        cache.clear()

        video_id = "dQw4w9WgXcQ"
        youtube_url = f"https://www.youtube.com/watch?v={video_id}"
        cache_key = f"transcript:video:{video_id}"

        # First call should fetch
        transcript1, error1 = get_transcript_with_diagnostics(
            youtube_url, mock_get_transcript
        )
        assert transcript1 == "Test transcript"
        assert error1 is None
        assert mock_get_transcript.call_count == 1

        # Second call should use cache
        transcript2, error2 = get_transcript_with_diagnostics(
            youtube_url, mock_get_transcript
        )
        assert transcript2 == "Test transcript"
        assert error2 is None
        assert mock_get_transcript.call_count == 1  # Not called again

    @patch("note_generator.views.get_transcript")
    def test_error_cached_to_reduce_retries(self, mock_get_transcript):
        """Test that errors are cached to reduce retries on rate limits."""
        from django.core.cache import cache

        mock_get_transcript.side_effect = Exception("HTTP 429: Too Many Requests")

        # Clear cache first
        cache.clear()

        video_id = "dQw4w9WgXcQ"
        youtube_url = f"https://www.youtube.com/watch?v={video_id}"

        # First call should attempt fetch
        transcript1, error1 = get_transcript_with_diagnostics(
            youtube_url, mock_get_transcript
        )
        assert transcript1 is None
        assert error1 is not None
        assert error1.error_code == "youtube_blocked"
        assert mock_get_transcript.call_count == 1

        # Second call should use cached error without retrying
        transcript2, error2 = get_transcript_with_diagnostics(
            youtube_url, mock_get_transcript
        )
        assert transcript2 is None
        assert error2 is not None
        assert mock_get_transcript.call_count == 1  # Not called again
