import json
from django.test import TestCase
from django.urls import reverse
from django.contrib.auth import get_user_model
from unittest.mock import patch
from note_generator.transcript_utils import NoTranscriptError


class TranscriptFailureTest(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username="u", password="p12345678")
        self.client.login(username="u", password="p12345678")

    @patch("note_generator.transcript_utils.get_transcript_with_diagnostics")
    @patch("note_generator.views.yt_title", return_value="Any Title")
    @patch(
        "note_generator.views.generate_blog_from_transcription",
        return_value="won't be used",
    )
    def test_transcript_missing_returns_502(self, _gen, _title, _transcript_diag):
        # Mock the function to return None transcript with error
        _transcript_diag.return_value = (None, NoTranscriptError())

        url = reverse("generate-notes")
        resp = self.client.post(
            url,
            data=json.dumps({"link": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"}),
            content_type="application/json",
        )

        assert resp.status_code == 502
        data = resp.json()
        assert "error_code" in data
        assert data["error_code"] == "no_transcript"
