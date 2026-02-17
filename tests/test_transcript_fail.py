import json
from django.test import TestCase
from django.urls import reverse
from django.contrib.auth import get_user_model
from unittest.mock import patch


class TranscriptFailureTest(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username="u", password="p12345678")
        self.client.login(username="u", password="p12345678")

    @patch("note_generator.views.get_transcript", return_value=None)
    @patch("note_generator.views.yt_title", return_value="Any Title")
    @patch(
        "note_generator.views.generate_blog_from_transcription",
        return_value="won't be used",
    )
    def test_transcript_missing_returns_502(self, _gen, _title, _transcript):
        url = reverse("generate-notes")
        resp = self.client.post(
            url,
            data=json.dumps({"link": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"}),
            content_type="application/json",
        )

        assert resp.status_code == 502
        assert "Transcript" in resp.json()["error"]
