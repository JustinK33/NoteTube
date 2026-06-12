import json
from django.test import TestCase
from django.urls import reverse
from django.contrib.auth import get_user_model
from django.core.cache import cache
from unittest.mock import patch
from note_generator.transcript_utils import NoTranscriptError


class TranscriptFailureTest(TestCase):
    def setUp(self):
        cache.clear()
        User = get_user_model()
        self.user = User.objects.create_user(username="u", password="p12345678")
        self.client.login(username="u", password="p12345678")

    @patch("note_generator.transcript_utils.get_transcript_with_diagnostics")
    @patch("note_generator.views.yt_title", return_value="Any Title")
    def test_no_transcript_surfaces_as_task_error(self, _title, mock_diag):
        mock_diag.return_value = (None, NoTranscriptError())

        resp = self.client.post(
            reverse("generate-notes"),
            data=json.dumps({"link": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"}),
            content_type="application/json",
        )

        # View enqueues immediately
        self.assertEqual(resp.status_code, 202)
        task_id = resp.json()["task_id"]

        # Task ran eagerly; result is available now
        status = self.client.get(f"/api/task-status/{task_id}/")
        data = status.json()
        self.assertEqual(data["status"], "failed")
        self.assertIsNone(data["note_id"])
