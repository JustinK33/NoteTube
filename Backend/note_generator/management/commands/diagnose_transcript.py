"""
Management command to diagnose transcript fetch issues.

Usage:
    python manage.py diagnose_transcript "https://www.youtube.com/watch?v=xyz"
"""

from django.core.management.base import BaseCommand, CommandError
from note_generator.views import get_transcript
from note_generator.transcript_utils import extract_video_id
import logging

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Diagnose transcript fetch issues for a YouTube video"

    def add_arguments(self, parser):
        parser.add_argument(
            "youtube_url",
            type=str,
            help="YouTube video URL to diagnose",
        )

    def handle(self, *args, **options):
        youtube_url = options["youtube_url"]

        self.stdout.write(
            self.style.SUCCESS(
                f"\n📋 Diagnosing transcript fetch for:\n   {youtube_url}\n"
            )
        )

        # Step 1: Extract video ID
        try:
            video_id = extract_video_id(youtube_url)
            self.stdout.write(self.style.SUCCESS(f"✓ Video ID extracted: {video_id}"))
        except ValueError as e:
            raise CommandError(f"✗ Failed to extract video ID: {e}")

        # Step 2: Attempt to fetch transcript
        self.stdout.write("\nAttempting to fetch transcript...")
        try:
            transcript = get_transcript(youtube_url)
            if transcript:
                self.stdout.write(
                    self.style.SUCCESS(
                        f"✓ Transcript fetched successfully ({len(transcript)} chars)"
                    )
                )
                self.stdout.write(f"\nFirst 500 characters:\n{transcript[:500]}")
            else:
                self.stdout.write(
                    self.style.WARNING("⚠ Transcript returned None (no captions?)")
                )
        except Exception as e:
            error_str = str(e).lower()
            error_type = type(e).__name__

            self.stdout.write(self.style.ERROR(f"✗ Fetch failed: {error_type}"))
            self.stdout.write(f"  Details: {e}\n")

            # Provide diagnostic hints
            if "403" in error_str:
                self.stdout.write(
                    self.style.WARNING(
                        "💡 HTTP 403 (Forbidden): YouTube is blocking this IP.\n"
                        "   Solution: Use a proxy/VPN or check AWS IP restrictions."
                    )
                )
            elif "429" in error_str:
                self.stdout.write(
                    self.style.WARNING(
                        "💡 HTTP 429 (Too Many Requests): Rate limited.\n"
                        "   Solution: Wait before retrying or use MP3 upload instead."
                    )
                )
            elif "captcha" in error_str:
                self.stdout.write(
                    self.style.WARNING(
                        "💡 CAPTCHA required: YouTube blocked as bot.\n"
                        "   Solution: Use MP3 upload or switch to a residential IP."
                    )
                )
            elif "connection" in error_str or "timeout" in error_str:
                self.stdout.write(
                    self.style.WARNING(
                        "💡 Connection/timeout error:\n"
                        "   Solution: Check EC2 internet connectivity."
                    )
                )
            else:
                self.stdout.write(
                    self.style.WARNING(f"💡 Unknown error. Check logs for details.")
                )

        self.stdout.write("\n")
