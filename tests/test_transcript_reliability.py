import json

import pytest
from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import Client, TestCase
from unittest.mock import patch

from note_generator.transcript_utils import (
    NoTranscriptError,
    TranscriptFetchError,
    YouTubeBlockedError,
    extract_video_id,
    get_transcript_with_diagnostics,
)


@pytest.fixture
def test_user():
    return User.objects.create_user(username="testuser", password="testpass123")


@pytest.fixture
def authenticated_client(test_user):
    client = Client()
    client.login(username="testuser", password="testpass123")
    return client


class TestExtractVideoId(TestCase):
    def test_extract_from_watch_url(self):
        assert (
            extract_video_id("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
            == "dQw4w9WgXcQ"
        )

    def test_extract_from_youtu_be(self):
        assert extract_video_id("https://youtu.be/dQw4w9WgXcQ") == "dQw4w9WgXcQ"

    def test_extract_from_shorts(self):
        assert (
            extract_video_id("https://www.youtube.com/shorts/dQw4w9WgXcQ")
            == "dQw4w9WgXcQ"
        )

    def test_extract_invalid_url(self):
        with pytest.raises(ValueError):
            extract_video_id("https://example.com/invalid")


@pytest.mark.django_db
class TestTranscriptErrorHandling(TestCase):
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


def _post_generate(client, link="https://www.youtube.com/watch?v=dQw4w9WgXcQ"):
    return client.post(
        "/generate-notes",
        data=json.dumps({"link": link}),
        content_type="application/json",
    )


def _task_result(client, task_id):
    return client.get(f"/api/task-status/{task_id}/").json()


@pytest.mark.django_db
class TestGenerateNoteEndpoint(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="testuser", password="testpass123"
        )
        self.client = Client()
        self.client.login(username="testuser", password="testpass123")
        cache.clear()

    def test_missing_youtube_link(self):
        resp = _post_generate(self.client, link="")
        assert resp.status_code == 400
        assert "error_code" in resp.json()

    def test_invalid_url_format(self):
        resp = _post_generate(self.client, link="not-a-youtube-link")
        assert resp.status_code == 400
        assert resp.json()["error_code"] == "invalid_url"

    def test_not_authenticated(self):
        self.client.logout()
        resp = _post_generate(self.client)
        assert resp.status_code in (302, 403)

    @patch("note_generator.transcript_utils.get_transcript_with_diagnostics")
    @patch("note_generator.views.yt_title", return_value="Test Video")
    def test_transcript_fetch_403_blocked(self, _title, mock_diag):
        mock_diag.return_value = (None, YouTubeBlockedError("HTTP 403 Forbidden"))

        resp = _post_generate(self.client)
        assert resp.status_code == 202
        result = _task_result(self.client, resp.json()["task_id"])

        assert result["status"] == "failed"
        assert "youtube_blocked" in result["error"] or "MP3" in result["error"]

    @patch("note_generator.transcript_utils.get_transcript_with_diagnostics")
    @patch("note_generator.views.yt_title", return_value="Test Video")
    def test_transcript_fetch_429_rate_limited(self, _title, mock_diag):
        mock_diag.return_value = (
            None,
            YouTubeBlockedError("HTTP 429 Too Many Requests"),
        )

        resp = _post_generate(self.client)
        assert resp.status_code == 202
        result = _task_result(self.client, resp.json()["task_id"])

        assert result["status"] == "failed"

    @patch("note_generator.transcript_utils.get_transcript_with_diagnostics")
    @patch("note_generator.views.yt_title", return_value="Test Video")
    def test_transcript_none_no_captions(self, _title, mock_diag):
        mock_diag.return_value = (None, NoTranscriptError())

        resp = _post_generate(self.client)
        assert resp.status_code == 202
        result = _task_result(self.client, resp.json()["task_id"])

        assert result["status"] == "failed"
        assert result["note_id"] is None

    @patch("note_generator.views.generate_blog_from_transcription")
    @patch(
        "note_generator.views.get_transcript", return_value="Test transcript content"
    )
    @patch("note_generator.views.yt_title", return_value="Test Video")
    def test_generation_failure(self, _title, _transcript, mock_generate):
        mock_generate.side_effect = Exception("OpenAI service unavailable")

        resp = _post_generate(self.client)
        assert resp.status_code == 202
        result = _task_result(self.client, resp.json()["task_id"])

        assert result["status"] == "failed"
        assert result["note_id"] is None

    @patch(
        "note_generator.views.generate_blog_from_transcription",
        return_value="# Generated Notes\n\nTest content",
    )
    @patch(
        "note_generator.views.get_transcript",
        return_value="Test transcript with content",
    )
    @patch("note_generator.views.yt_title", return_value="Test Video Title")
    def test_successful_note_generation(self, _title, _transcript, _generate):
        resp = _post_generate(self.client)
        assert resp.status_code == 202
        result = _task_result(self.client, resp.json()["task_id"])

        assert result["status"] == "done"
        assert result["note_id"] is not None


@pytest.mark.django_db
class TestTranscriptCaching(TestCase):
    @patch("note_generator.views.get_transcript")
    def test_transcript_cached_on_success(self, mock_get_transcript):
        from django.core.cache import cache

        mock_get_transcript.return_value = "Test transcript"
        cache.clear()

        youtube_url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"

        transcript1, error1 = get_transcript_with_diagnostics(
            youtube_url, mock_get_transcript
        )
        assert transcript1 == "Test transcript"
        assert error1 is None
        assert mock_get_transcript.call_count == 1

        transcript2, error2 = get_transcript_with_diagnostics(
            youtube_url, mock_get_transcript
        )
        assert transcript2 == "Test transcript"
        assert error2 is None
        assert mock_get_transcript.call_count == 1  # cache hit

    @patch("note_generator.views.get_transcript")
    def test_error_cached_to_reduce_retries(self, mock_get_transcript):
        from django.core.cache import cache

        mock_get_transcript.side_effect = Exception("HTTP 429: Too Many Requests")
        cache.clear()

        youtube_url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"

        transcript1, error1 = get_transcript_with_diagnostics(
            youtube_url, mock_get_transcript
        )
        assert transcript1 is None
        assert error1.error_code == "youtube_blocked"
        assert mock_get_transcript.call_count == 1

        transcript2, error2 = get_transcript_with_diagnostics(
            youtube_url, mock_get_transcript
        )
        assert transcript2 is None
        assert mock_get_transcript.call_count == 1  # cache hit
