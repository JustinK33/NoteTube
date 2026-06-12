import logging
import os
import shutil

from celery import shared_task
from django.contrib.auth.models import User

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=0, name="note_generator.generate_note")
def generate_note_task(self, user_id: int, yt_link: str):
    from note_generator.models import NotePost
    from note_generator.views import (
        generate_blog_from_transcription,
        get_transcript,
        yt_title,
    )
    from note_generator.grpc_client import process_transcript_via_grpc
    from note_generator.transcript_utils import get_transcript_with_diagnostics
    from note_generator.utils.cache_utils import safe_cache_delete

    try:
        user = User.objects.get(pk=user_id)
    except User.DoesNotExist:
        return {
            "note_id": None,
            "error": "User not found",
            "error_code": "user_not_found",
        }

    try:
        title = yt_title(yt_link)
    except Exception as e:
        logger.warning(f"generate_note_task: yt_title failed for {yt_link}: {e}")
        title = f"YouTube Note ({yt_link[:50]})"

    transcript, transcript_error = get_transcript_with_diagnostics(
        yt_link, get_transcript
    )

    if transcript_error:
        error_msg = transcript_error.message
        if transcript_error.error_code in ("youtube_blocked", "no_transcript"):
            error_msg += (
                '\n\n💡 Tip: Try uploading an MP3 file instead on the "Upload MP3" tab.'
            )
        return {
            "note_id": None,
            "error": error_msg,
            "error_code": transcript_error.error_code,
        }

    if not transcript:
        return {
            "note_id": None,
            "error": "Transcript unavailable. Try MP3 upload or paste transcript.",
            "error_code": "no_transcript",
        }

    try:
        note_content = process_transcript_via_grpc(
            transcript_text=transcript, source_url=yt_link, title=title
        )
        if not note_content:
            raise RuntimeError("gRPC returned empty content")
    except Exception as e:
        logger.warning(f"generate_note_task: gRPC failed, falling back to OpenAI: {e}")
        try:
            note_content = generate_blog_from_transcription(transcript)
            if not note_content:
                raise RuntimeError("OpenAI returned empty content")
        except Exception as fallback_error:
            logger.exception(f"generate_note_task: generation failed: {fallback_error}")
            return {
                "note_id": None,
                "error": "Note generation service temporarily unavailable.",
                "error_code": "generation_failed",
            }

    try:
        note = NotePost.objects.create(
            user=user,
            youtube_title=title,
            youtube_link=yt_link,
            generated_content=note_content,
        )
        safe_cache_delete(f"notes:list:user:{user_id}")
        return {"note_id": note.id, "error": None}
    except Exception as e:
        logger.exception(f"generate_note_task: failed to save note: {e}")
        return {
            "note_id": None,
            "error": "Could not save notes. Please try again.",
            "error_code": "save_failed",
        }


@shared_task(bind=True, max_retries=0, name="note_generator.mp3_to_notes")
def mp3_to_notes_task(
    self, user_id: int, audio_file_path: str, title: str, temp_dir: str
):
    import assemblyai as aai

    from note_generator.models import NotePost
    from note_generator.views import generate_blog_from_transcription
    from note_generator.utils.cache_utils import safe_cache_delete

    try:
        user = User.objects.get(pk=user_id)
    except User.DoesNotExist:
        shutil.rmtree(temp_dir, ignore_errors=True)
        return {
            "note_id": None,
            "error": "User not found",
            "error_code": "user_not_found",
        }

    try:
        # api_key is set at module level in views.py but workers don't import views on startup
        aai.settings.api_key = os.getenv("APIKEY")
        transcriber = aai.Transcriber()
        transcript = transcriber.transcribe(audio_file_path)
        if not transcript.text:
            raise RuntimeError("AssemblyAI returned empty transcript")

        note_content = generate_blog_from_transcription(transcript.text)
        if not note_content:
            raise RuntimeError("OpenAI returned empty note content")

        note = NotePost.objects.create(
            user=user,
            youtube_title=title,
            youtube_link="",
            generated_content=note_content,
        )
        safe_cache_delete(f"notes:list:user:{user_id}")
        return {"note_id": note.id, "error": None}

    except Exception as e:
        logger.exception(f"mp3_to_notes_task: failed for user {user_id}: {e}")
        return {
            "note_id": None,
            "error": f"MP3 processing failed: {e}",
            "error_code": "mp3_failed",
        }
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


@shared_task(
    bind=True, max_retries=3, default_retry_delay=10, name="note_generator.embed_note"
)
def embed_note_task(self, note_id: int):
    from note_generator.models import NotePost
    from note_generator.rag.embed import embed_note

    try:
        note = NotePost.objects.get(pk=note_id)
        embed_note(note)
    except NotePost.DoesNotExist:
        logger.warning(
            f"embed_note_task: note {note_id} gone before embedding, skipping"
        )
    except Exception as exc:
        logger.warning(f"embed_note_task: attempt failed for note {note_id}: {exc}")
        raise self.retry(exc=exc)


@shared_task(bind=True, max_retries=0, name="note_generator.export_to_notion")
def export_to_notion_task(self, note_id: int, user_id: int):
    from note_generator.models import NotePost, UserProfile
    from note_generator.utils.notion_export import (
        NotionExportError,
        export_note_to_notion,
    )

    try:
        note = NotePost.objects.get(pk=note_id, user_id=user_id)
    except NotePost.DoesNotExist:
        return {"url": None, "error": "Note not found", "error_code": "not_found"}

    profile, _ = UserProfile.objects.get_or_create(user_id=user_id)

    try:
        page_url = export_note_to_notion(
            token=profile.notion_token,
            parent_page_id=profile.notion_parent_page_id,
            title=note.youtube_title,
            content=note.generated_content,
            source_url=note.youtube_link or "",
        )
        return {"url": page_url, "error": None}
    except NotionExportError as e:
        logger.warning(
            f"export_to_notion_task: Notion export failed for note {note_id}: {e}"
        )
        return {"url": None, "error": str(e), "error_code": "notion_failed"}
    except Exception as e:
        logger.exception(
            f"export_to_notion_task: unexpected error for note {note_id}: {e}"
        )
        return {"url": None, "error": f"Unexpected error: {e}", "error_code": "unknown"}
