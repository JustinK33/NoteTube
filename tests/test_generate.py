from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth import get_user_model
import json


class NoteGenerationTest(TestCase):
    def setUp(self):
        self.client = Client()

        # 🔥 ADD THIS
        User = get_user_model()
        self.user = User.objects.create_user(
            username="testuser", password="testpass123"
        )
        self.client.login(username="testuser", password="testpass123")

    def test_generate_notes_accepts_youtube_url_field(self):
        url = reverse("generate-notes")
        youtube_url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"

        response = self.client.post(
            url,
            json.dumps({"link": youtube_url}),
            content_type="application/json",
        )

        data = response.json()
        # We expect it NOT to be missing/empty
        assert not (
            response.status_code == 400
            and data.get("error", "").startswith("Invalid YouTube link: ")
        )

    def test_generate_notes_missing_url_returns_400(self):
        url = reverse("generate-notes")
        response = self.client.post(
            url, json.dumps({}), content_type="application/json"
        )
        assert response.status_code == 400
        data = response.json()
        assert "error_code" in data
        assert data["error_code"] == "invalid_url"
