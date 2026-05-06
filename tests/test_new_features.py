import os
import sys
import django
from unittest.mock import patch
from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth.models import User

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "Backend"))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "notetube.testing_settings")
django.setup()

from note_generator.models import NotePost, UserProfile
from note_generator.utils.notion_export import (
    text_to_notion_blocks,
    export_note_to_notion,
    NotionExportError,
)


class NoteCreateTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("alice", "a@a.com", "pw")
        self.client.force_login(self.user)

    def test_get_renders_form(self):
        response = self.client.get("/note-create")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Create Note")

    def test_post_creates_note_and_redirects(self):
        response = self.client.post(
            "/note-create",
            {"youtube_title": "My Note", "generated_content": "Body text"},
        )
        self.assertEqual(response.status_code, 302)
        note = NotePost.objects.get(user=self.user, youtube_title="My Note")
        self.assertEqual(note.generated_content, "Body text")
        self.assertEqual(note.youtube_link, "")
        self.assertIn(f"/note-details/{note.pk}/", response.url)

    def test_post_blank_fields_shows_error(self):
        response = self.client.post(
            "/note-create",
            {"youtube_title": "", "generated_content": ""},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(NotePost.objects.count(), 0)

    def test_post_whitespace_only_treated_as_empty(self):
        response = self.client.post(
            "/note-create",
            {"youtube_title": "   ", "generated_content": "   "},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(NotePost.objects.count(), 0)

    def test_anonymous_user_redirected(self):
        self.client.logout()
        response = self.client.get("/note-create")
        self.assertEqual(response.status_code, 302)
        self.assertIn("login", response.url.lower())

    def test_overlong_title_rejected(self):
        # NotePost.youtube_title is CharField(max_length=300).
        # SQLite (test DB) doesn't enforce this, but PostgreSQL (prod) does.
        # The view should reject app-side rather than letting it crash the DB.
        long_title = "x" * 500
        response = self.client.post(
            "/note-create",
            {"youtube_title": long_title, "generated_content": "Body"},
        )
        self.assertEqual(response.status_code, 200)  # form re-rendered, not redirect
        self.assertEqual(NotePost.objects.count(), 0)


class NotionSettingsTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("bob", "b@b.com", "pw")
        self.client.force_login(self.user)

    def test_get_creates_profile_lazily(self):
        self.assertFalse(UserProfile.objects.filter(user=self.user).exists())
        response = self.client.get("/notion-settings")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(UserProfile.objects.filter(user=self.user).exists())

    def test_post_saves_credentials(self):
        response = self.client.post(
            "/notion-settings",
            {
                "notion_token": "secret_abc123",
                "notion_parent_page_id": "page-xyz-456",
            },
        )
        self.assertEqual(response.status_code, 302)
        profile = UserProfile.objects.get(user=self.user)
        self.assertEqual(profile.notion_token, "secret_abc123")
        self.assertEqual(profile.notion_parent_page_id, "page-xyz-456")
        self.assertTrue(profile.has_notion_configured())

    def test_post_clears_credentials(self):
        UserProfile.objects.create(
            user=self.user,
            notion_token="old",
            notion_parent_page_id="old-id",
        )
        self.client.post(
            "/notion-settings",
            {"notion_token": "", "notion_parent_page_id": ""},
        )
        profile = UserProfile.objects.get(user=self.user)
        self.assertFalse(profile.has_notion_configured())


class NotionExportEndpointTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("carol", "c@c.com", "pw")
        self.client.force_login(self.user)
        self.note = NotePost.objects.create(
            user=self.user,
            youtube_title="T",
            youtube_link="",
            generated_content="content",
        )

    def test_get_returns_405(self):
        response = self.client.get(f"/note-export-notion/{self.note.pk}/")
        self.assertEqual(response.status_code, 405)

    def test_unconfigured_user_gets_400(self):
        response = self.client.post(f"/note-export-notion/{self.note.pk}/")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error_code"], "notion_not_configured")

    def test_other_users_note_returns_404(self):
        other = User.objects.create_user("eve", "e@e.com", "pw")
        other_note = NotePost.objects.create(
            user=other,
            youtube_title="other",
            youtube_link="",
            generated_content="x",
        )
        response = self.client.post(f"/note-export-notion/{other_note.pk}/")
        self.assertEqual(response.status_code, 404)

    @patch("note_generator.views.export_note_to_notion")
    def test_happy_path_returns_url(self, mock_export):
        mock_export.return_value = "https://www.notion.so/created-page"
        UserProfile.objects.create(
            user=self.user,
            notion_token="secret_t",
            notion_parent_page_id="parent-id",
        )
        response = self.client.post(f"/note-export-notion/{self.note.pk}/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["url"], "https://www.notion.so/created-page")
        mock_export.assert_called_once()

    @patch("note_generator.views.export_note_to_notion")
    def test_export_error_returns_502(self, mock_export):
        mock_export.side_effect = NotionExportError("Bad token")
        UserProfile.objects.create(
            user=self.user,
            notion_token="bad",
            notion_parent_page_id="parent",
        )
        response = self.client.post(f"/note-export-notion/{self.note.pk}/")
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json()["error_code"], "notion_failed")


class NoteDetailsCsrfCookieTests(TestCase):
    """The Notion export button on note_details POSTs with X-CSRFToken header.
    The browser only has the csrftoken cookie if Django's CSRF machinery has
    been told to set it. If users land on /note-details/<pk>/ without that
    cookie, the export will 403."""

    def setUp(self):
        self.user = User.objects.create_user("dan", "d@d.com", "pw")
        self.client.force_login(self.user)
        self.note = NotePost.objects.create(
            user=self.user,
            youtube_title="T",
            youtube_link="",
            generated_content="x",
        )

    def test_note_details_sets_csrf_cookie(self):
        response = self.client.get(f"/note-details/{self.note.pk}/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("csrftoken", response.cookies)

    def test_missing_note_redirects_to_saved_notes(self):
        # Bug: previously redirected to /note-list which doesn't exist.
        response = self.client.get("/note-details/99999/")
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.endswith("/saved-notes"))


class TextToNotionBlocksTests(TestCase):
    def test_empty_input(self):
        self.assertEqual(text_to_notion_blocks(""), [])

    def test_only_blank_lines(self):
        self.assertEqual(text_to_notion_blocks("\n\n\n"), [])

    def test_heading_keyword_becomes_h2(self):
        blocks = text_to_notion_blocks("TL;DR")
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0]["type"], "heading_2")

    def test_bullet_classified(self):
        blocks = text_to_notion_blocks("- a bullet")
        self.assertEqual(blocks[0]["type"], "bulleted_list_item")
        text = blocks[0]["bulleted_list_item"]["rich_text"][0]["text"]["content"]
        self.assertEqual(text, "a bullet")

    def test_numbered_classified(self):
        blocks = text_to_notion_blocks("1) Step one\n2. Step two")
        self.assertEqual(blocks[0]["type"], "numbered_list_item")
        self.assertEqual(blocks[1]["type"], "numbered_list_item")

    def test_paragraph_default(self):
        blocks = text_to_notion_blocks("just a normal sentence")
        self.assertEqual(blocks[0]["type"], "paragraph")

    def test_long_text_chunks_under_2000(self):
        long = "x" * 5000
        blocks = text_to_notion_blocks(long)
        rich = blocks[0]["paragraph"]["rich_text"]
        self.assertGreater(len(rich), 1)
        for fragment in rich:
            self.assertLessEqual(len(fragment["text"]["content"]), 2000)


class ExportNoteToNotionTests(TestCase):
    @patch("note_generator.utils.notion_export.requests.post")
    def test_success_returns_url(self, mock_post):
        mock_post.return_value.status_code = 200
        mock_post.return_value.json.return_value = {"url": "https://notion.so/page"}
        url = export_note_to_notion(
            token="t",
            parent_page_id="p",
            title="T",
            content="hello",
        )
        self.assertEqual(url, "https://notion.so/page")

    @patch("note_generator.utils.notion_export.requests.post")
    def test_4xx_raises(self, mock_post):
        mock_post.return_value.status_code = 401
        mock_post.return_value.json.return_value = {"message": "unauthorized"}
        with self.assertRaises(NotionExportError):
            export_note_to_notion(
                token="bad", parent_page_id="p", title="T", content="x"
            )

    @patch("note_generator.utils.notion_export.requests.post")
    def test_payload_sent_correctly(self, mock_post):
        mock_post.return_value.status_code = 200
        mock_post.return_value.json.return_value = {"url": "u"}
        export_note_to_notion(
            token="secret",
            parent_page_id="parent-123",
            title="My Title",
            content="- bullet",
            source_url="https://yt.com/v/abc",
        )
        kwargs = mock_post.call_args.kwargs
        self.assertEqual(
            kwargs["headers"]["Authorization"], "Bearer secret"
        )
        self.assertEqual(kwargs["json"]["parent"]["page_id"], "parent-123")
        # Source URL should be the first block.
        first_block = kwargs["json"]["children"][0]
        self.assertEqual(first_block["type"], "paragraph")
        self.assertIn(
            "https://yt.com/v/abc",
            first_block["paragraph"]["rich_text"][0]["text"]["content"],
        )
