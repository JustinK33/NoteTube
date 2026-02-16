import os
import sys
import django
from django.test import TestCase
from django.urls import reverse

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "Backend"))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "notetube.settings")
django.setup()


class SmokeTest(TestCase):
    def test_homepage_loads(self):
        response = self.client.get(reverse("home"))
        self.assertEqual(response.status_code, 200)
