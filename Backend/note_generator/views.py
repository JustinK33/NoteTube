from django.contrib.auth import authenticate, login, logout
from django.shortcuts import render, redirect
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.views.decorators.csrf import csrf_exempt, ensure_csrf_cookie
from django.http import JsonResponse, HttpResponse
from django.conf import settings
from fastapi import HTTPException
import json, os, time
from pytubefix import YouTube
import assemblyai as aai
import openai
from .models import NotePost, UserProfile
import traceback
import tempfile
from note_generator.utils.cache_utils import (
    cached_get_or_set,
    safe_cache_get,
    safe_cache_set,
    safe_cache_delete,
)
from django.core.cache import cache
import re
import logging
import subprocess
from fastapi.responses import FileResponse
import tempfile
import shutil
from youtube_transcript_api import YouTubeTranscriptApi
from django.utils.text import slugify
from io import BytesIO
from textwrap import wrap
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter

aai.settings.api_key = os.getenv("APIKEY")


def home(request):
    return render(request, "home.html")


@login_required
def index(request):
    return render(request, "index.html")


# learn this later
logger = logging.getLogger(__name__)

YOUTUBE_ID_RE = re.compile(r"([0-9A-Za-z_-]{11})")


def normalize_youtube_url(raw: str) -> str:
    raw = (raw or "").strip()

    # If they sent just the 11-char id
    if len(raw) == 11 and YOUTUBE_ID_RE.fullmatch(raw):
        vid = raw
        return f"https://www.youtube.com/watch?v={vid}"

    patterns = [
        r"youtu\.be/([0-9A-Za-z_-]{11})",
        r"youtube\.com/watch\?v=([0-9A-Za-z_-]{11})",
        r"[?&]v=([0-9A-Za-z_-]{11})",
        r"youtube\.com/embed/([0-9A-Za-z_-]{11})",
        r"youtube\.com/shorts/([0-9A-Za-z_-]{11})",
    ]

    for p in patterns:
        m = re.search(p, raw)
        if m:
            vid = m.group(1)
            return f"https://www.youtube.com/watch?v={vid}"

    raise ValueError(f"Invalid YouTube link: {raw}")


@login_required
@csrf_exempt
def mp3_to_notes(request):
    if request.method != "POST":
        return JsonResponse({"error": "Invalid request method"}, status=405)

    try:
        mp3_file = request.FILES.get("mp3_file")
        title = request.POST.get("title", "Untitled Note")

        if not mp3_file:
            return JsonResponse({"error": "No MP3 file provided"}, status=400)
        if not mp3_file.name.endswith(".mp3"):
            return JsonResponse({"error": "File must be an MP3"}, status=400)

        # Save to temp dir — the task owns cleanup from here
        temp_dir = tempfile.mkdtemp()
        mp3_path = os.path.join(temp_dir, mp3_file.name)
        with open(mp3_path, "wb") as f:
            for chunk in mp3_file.chunks():
                f.write(chunk)

        from note_generator.tasks import mp3_to_notes_task

        task = mp3_to_notes_task.delay(request.user.id, mp3_path, title, temp_dir)
        return JsonResponse({"task_id": task.id, "status": "processing"}, status=202)

    except Exception as e:
        return JsonResponse({"error": f"Error starting task: {str(e)}"}, status=500)


def get_mp3_transcript(mp3_path: str) -> str:
    # since they upload a mp3 we dont need to store it and just get a transcript from it
    if not mp3_path.endswith(".mp3"):
        raise ValueError("File must be an .mp3")

    if not os.path.exists(mp3_path):
        raise FileNotFoundError(f"{mp3_path} not found")

    transcriber = aai.Transcriber()
    transcript = transcriber.transcribe(mp3_path)

    if transcript.text is None:  # fixes type check error
        raise RuntimeError("Transcription error: no text returned")

    return transcript.text


@login_required
@csrf_exempt
def generate_note(request):
    if request.method != "POST":
        return JsonResponse({"error": "Invalid request method"}, status=405)

    rate_limit_key = f"rate_limit:user:{request.user.id}"
    last_request = safe_cache_get(rate_limit_key)
    if last_request:
        remaining = 600 - (time.time() - last_request)
        if remaining > 0:
            minutes = int(remaining // 60)
            seconds = int(remaining % 60)
            return JsonResponse(
                {
                    "error_code": "rate_limited",
                    "message": f"Please wait {minutes}m {seconds}s before generating another note.",
                },
                status=429,
            )

    try:
        data = json.loads(request.body)
        yt_link_raw = data.get("link", "")
        try:
            yt_link = normalize_youtube_url(yt_link_raw)
        except ValueError as e:
            return JsonResponse(
                {"error_code": "invalid_url", "message": str(e)}, status=400
            )
    except (KeyError, json.JSONDecodeError):
        return JsonResponse(
            {"error_code": "invalid_request", "message": "Invalid data sent"},
            status=400,
        )

    # set before enqueueing so duplicate submissions are blocked while the task runs
    safe_cache_set(rate_limit_key, time.time(), timeout=600)

    from note_generator.tasks import generate_note_task

    task = generate_note_task.delay(request.user.id, yt_link)
    return JsonResponse({"task_id": task.id, "status": "processing"}, status=202)


@login_required
def task_status(request, task_id):
    from celery.result import AsyncResult

    result = AsyncResult(task_id)
    state = result.state
    meta = result.info or {}

    if state == "PENDING":
        return JsonResponse({"status": "pending", "note_id": None, "error": None})

    if state in ("STARTED", "PROGRESS"):
        return JsonResponse({"status": "processing", "note_id": None, "error": None})

    if state == "SUCCESS":
        if isinstance(meta, dict):
            error = meta.get("error")
            if error:
                return JsonResponse(
                    {"status": "failed", "note_id": None, "error": error}
                )
            response_data = {
                "status": "done",
                "note_id": meta.get("note_id"),
                "error": None,
            }
            if meta.get("url"):
                response_data["url"] = meta["url"]
            return JsonResponse(response_data)
        return JsonResponse({"status": "done", "note_id": None, "error": None})

    if state == "FAILURE":
        error = (
            str(meta)
            if not isinstance(meta, dict)
            else meta.get("error", "Task failed unexpectedly")
        )
        return JsonResponse({"status": "failed", "note_id": None, "error": error})

    # RETRY or unknown custom state
    return JsonResponse({"status": "processing", "note_id": None, "error": None})


@login_required
def notion_settings(request):
    profile, _ = UserProfile.objects.get_or_create(user=request.user)

    if request.method == "POST":
        token = (request.POST.get("notion_token") or "").strip()
        parent_id = (request.POST.get("notion_parent_page_id") or "").strip()

        profile.notion_token = token
        profile.notion_parent_page_id = parent_id
        profile.save(update_fields=["notion_token", "notion_parent_page_id"])

        if token and parent_id:
            messages.success(request, "Notion settings saved.")
        else:
            messages.info(request, "Notion settings cleared.")

        return redirect("notion-settings")

    return render(request, "notion_settings.html", {"profile": profile})


@login_required
def note_create(request):
    if request.method == "POST":
        title = (request.POST.get("youtube_title") or "").strip()
        content = (request.POST.get("generated_content") or "").strip()

        if not title or not content:
            messages.error(request, "Title and content are both required.")
            return render(
                request,
                "note_create.html",
                {"form_title": title, "form_content": content},
            )

        # CharField(max_length=300) is enforced at the DB level on PostgreSQL.
        if len(title) > 300:
            messages.error(request, "Title must be 300 characters or fewer.")
            return render(
                request,
                "note_create.html",
                {"form_title": title, "form_content": content},
            )

        new_note = NotePost.objects.create(
            user=request.user,
            youtube_title=title,
            youtube_link="",
            generated_content=content,
        )

        safe_cache_delete(f"notes:list:user:{request.user.id}")

        return redirect("note-details", pk=new_note.pk)

    return render(request, "note_create.html")


@login_required
@ensure_csrf_cookie
def note_list(request):
    key = f"notes:list:user:{request.user.id}"

    notetube_post = cached_get_or_set(
        key=key,
        timeout=60,
        compute=lambda: list(
            NotePost.objects.filter(user=request.user).order_by("-id")
        ),
    )
    return render(request, "notes.html", {"notetube_post": notetube_post})


@login_required
@ensure_csrf_cookie
def note_details(request, pk):
    key = f"notes:detail:user:{request.user.id}:pk:{pk}"

    try:
        note_post_detail = cached_get_or_set(
            key=key,
            timeout=300,
            compute=lambda: NotePost.objects.get(id=pk, user=request.user),
        )
    except NotePost.DoesNotExist:
        return redirect("saved-notes")

    return render(request, "note_details.html", {"note_post_detail": note_post_detail})


@login_required
def note_edit(request, pk):
    try:
        note_post = NotePost.objects.get(id=pk, user=request.user)
    except NotePost.DoesNotExist:
        return redirect("saved-notes")

    if request.method == "POST":
        new_title = (
            request.POST.get("youtube_title", "").strip() or note_post.youtube_title
        )
        new_content = request.POST.get("generated_content", "").strip()

        if not new_content:
            messages.error(request, "Note content cannot be empty")
            return render(request, "note_edit.html", {"note_post": note_post})

        note_post.youtube_title = new_title
        note_post.generated_content = new_content
        note_post.save(update_fields=["youtube_title", "generated_content"])

        # Invalidate related caches after manual edits.
        safe_cache_delete(f"notes:list:user:{request.user.id}")
        safe_cache_delete(f"notes:detail:user:{request.user.id}:pk:{pk}")

        messages.success(request, "Note updated successfully")
        return redirect("note-details", pk=pk)

    return render(request, "note_edit.html", {"note_post": note_post})


@login_required
@csrf_exempt
def note_delete(request, pk):
    if request.method != "POST":
        return JsonResponse(
            {"error_code": "method_not_allowed", "message": "POST required"},
            status=405,
        )

    try:
        note_post = NotePost.objects.get(id=pk, user=request.user)
    except NotePost.DoesNotExist:
        return JsonResponse(
            {"error_code": "not_found", "message": "Note not found"},
            status=404,
        )

    note_post.delete()

    safe_cache_delete(f"notes:list:user:{request.user.id}")
    safe_cache_delete(f"notes:detail:user:{request.user.id}:pk:{pk}")

    return JsonResponse({"ok": True})


@login_required
def note_export(request, pk):
    try:
        note_post = NotePost.objects.get(id=pk, user=request.user)
    except NotePost.DoesNotExist:
        return redirect("saved-notes")

    export_format = (request.GET.get("format") or "txt").lower()
    if export_format not in {"txt", "md", "pdf"}:
        export_format = "txt"

    safe_title = slugify(note_post.youtube_title) or f"note-{pk}"
    created_display = note_post.created_at.strftime("%Y-%m-%d %H:%M:%S")

    if export_format == "md":
        body = (
            f"# {note_post.youtube_title}\n\n"
            f"- Created: {created_display}\n"
            f"- Source: {note_post.youtube_link or 'N/A'}\n\n"
            f"## Notes\n\n{note_post.generated_content}\n"
        )
        content_type = "text/markdown; charset=utf-8"
        filename = f"{safe_title}.md"
        response = HttpResponse(body, content_type=content_type)
    elif export_format == "txt":
        body = (
            f"Title: {note_post.youtube_title}\n"
            f"Created: {created_display}\n"
            f"Source: {note_post.youtube_link or 'N/A'}\n"
            f"\n---\n\n"
            f"{note_post.generated_content}\n"
        )
        content_type = "text/plain; charset=utf-8"
        filename = f"{safe_title}.txt"
        response = HttpResponse(body, content_type=content_type)
    elif export_format == "pdf":
        buffer = BytesIO()
        pdf = canvas.Canvas(buffer, pagesize=letter)
        width, height = letter
        y = height - 50

        # Header
        pdf.setFont("Helvetica-Bold", 16)
        pdf.drawString(50, y, note_post.youtube_title)
        y -= 24

        pdf.setFont("Helvetica", 10)
        pdf.drawString(50, y, f"Created: {created_display}")
        y -= 14
        pdf.drawString(50, y, f"Source: {note_post.youtube_link or 'N/A'}")
        y -= 24

        pdf.setFont("Helvetica-Bold", 12)
        pdf.drawString(50, y, "Notes")
        y -= 18

        pdf.setFont("Helvetica", 10)
        for paragraph in note_post.generated_content.splitlines() or [""]:
            wrapped_lines = wrap(paragraph, width=105) or [""]
            for line in wrapped_lines:
                if y < 50:
                    pdf.showPage()
                    pdf.setFont("Helvetica", 10)
                    y = height - 50
                pdf.drawString(50, y, line)
                y -= 13
            y -= 4

        pdf.save()
        buffer.seek(0)
        response = HttpResponse(buffer.getvalue(), content_type="application/pdf")
        filename = f"{safe_title}.pdf"

    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


@login_required
@csrf_exempt
def note_export_notion(request, pk):
    if request.method != "POST":
        return JsonResponse(
            {"error_code": "method_not_allowed", "message": "POST required"}, status=405
        )

    try:
        NotePost.objects.get(id=pk, user=request.user)
    except NotePost.DoesNotExist:
        return JsonResponse(
            {"error_code": "not_found", "message": "Note not found"}, status=404
        )

    profile, _ = UserProfile.objects.get_or_create(user=request.user)
    if not profile.has_notion_configured():
        return JsonResponse(
            {
                "error_code": "notion_not_configured",
                "message": "Connect Notion in settings before exporting.",
            },
            status=400,
        )

    from note_generator.tasks import export_to_notion_task

    task = export_to_notion_task.delay(pk, request.user.id)
    return JsonResponse({"task_id": task.id, "status": "processing"}, status=202)


def yt_title(link):
    try:
        yt = YouTube(link)
        title = yt.title
        return title
    except Exception as e:
        logger.warning(f"yt_title failed: {e}")
        raise


import subprocess, tempfile, os, glob


def download_audio(link: str) -> str:
    tmpdir = tempfile.gettempdir()
    outtmpl = os.path.join(tmpdir, "%(id)s.%(ext)s")
    cookies_file = (getattr(settings, "YTDLP_COOKIES_FILE", "") or "").strip()

    # downloads best audio; yt-dlp chooses an audio container (webm/m4a usually)
    cmd = [
        "yt-dlp",
        "--user-agent",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "-o",
        outtmpl,
        link,
    ]

    if cookies_file:
        if os.path.isfile(cookies_file):
            cmd[1:1] = ["--cookies", cookies_file]
            logger.info(f"yt-dlp cookies enabled from: {cookies_file}")
        else:
            logger.warning(f"YTDLP_COOKIES_FILE not found: {cookies_file}")

    logger.info(f"Running yt-dlp: {' '.join(cmd)}")
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        logger.error(f"yt-dlp failed: {res.stderr}")
        raise RuntimeError(
            f"yt-dlp failed:\nSTDOUT:\n{res.stdout}\nSTDERR:\n{res.stderr}"
        )

    # find the newest downloaded file in /tmp (simple + reliable)
    candidates = sorted(
        glob.glob(os.path.join(tmpdir, "*.*")), key=os.path.getmtime, reverse=True
    )
    if not candidates:
        raise RuntimeError("yt-dlp succeeded but no file found in temp dir")

    return candidates[0]


# we gon use assembly ai to get the transcription
def get_transcript(link):
    # Prefer native caption transcript first; this avoids yt-dlp for many videos.
    try:
        video_match = re.search(r"[?&]v=([0-9A-Za-z_-]{11})", link)
        video_id = video_match.group(1) if video_match else None
        if video_id:
            transcript_data = YouTubeTranscriptApi().fetch(video_id)
            transcript_text = " ".join(
                chunk.text.strip() for chunk in transcript_data
            ).strip()
            if transcript_text:
                return transcript_text
    except Exception as e:
        logger.warning(f"YouTubeTranscriptApi failed, falling back to yt-dlp: {e}")

    audio_file = download_audio(link)
    try:
        transcriber = aai.Transcriber()
        transcript = transcriber.transcribe(audio_file)
        return transcript.text
    finally:
        if audio_file and os.path.exists(audio_file):
            os.remove(audio_file)


def generate_blog_from_transcription(transcription):
    openai.api_key = os.getenv("OPENAI_API_KEY")

    prompt = f"""You are a strict transcript-based note writer.

I will paste a YouTube transcript. Create clean, exam-ready notes using ONLY what appears in the transcript.
If something is missing or unclear, write: [UNCLEAR IN TRANSCRIPT: ...]. Do not add outside facts.

FORMATTING RULES (STRICT)
- Output must be PLAIN TEXT only.
- Do NOT use Markdown at all (no **bold**, no tables, no code fences).
- Use the exact indentation and bullet styles shown below.
- Put a blank line between sections.
- Wrap long lines naturally (don’t make one giant paragraph).

BULLET STYLE
- Use "-" for bullets.
- Use two spaces before sub-bullets.
Example:
- Main bullet
  - Sub bullet

OUTPUT FORMAT (exact headings + structure)

TL;DR
- (5 bullets)

KEY TERMS
- Term: definition.
  - Common mistake: ... (only if implied in transcript)

HOW IT WORKS
- (3–6 bullets)

STEP-BY-STEP
1) ...
2) ...
(If none, write exactly: None found in transcript.)

QUICK CHECK QUIZ (with answers)
Q1) Question?
A1) Answer.

Q2) Question?
A2) Answer.
(8–10 total)

ANCHORS
- "short quote snippet" -> what it teaches
(6–10 total)

Now wait. Here is the transcript:
{transcription}
"""

    response = openai.completions.create(
        model="gpt-4.1-nano", prompt=prompt, max_tokens=1000
    )
    generated_content = response.choices[0].text.strip()
    return generated_content


def user_login(request):
    if request.method == "POST":
        username = request.POST["username"]
        password = request.POST["password"]

        user = authenticate(request, username=username, password=password)
        if user is not None:
            login(request, user)
            return redirect("/index")
        else:
            # Use POST-Redirect-GET pattern to avoid browser "resubmit form" warnings
            messages.error(request, "Invalid username or password")
            return redirect("login")
    return render(request, "login.html")


def user_signup(request):
    if request.method == "POST":
        username = request.POST["username"]
        email = request.POST["email"]
        password = request.POST["password"]
        repeatPassword = request.POST["repeatPassword"]

        if password != repeatPassword:
            messages.error(request, "Passwords do not match")
            return redirect("signup")
        # !! keep things seperated too much in one try: broke the signup keep the logic seperate
        try:
            user = User.objects.create_user(username, email, password)
            user.save()
        except Exception as e:
            traceback.print_exc()
            messages.error(request, f"Error creating account: {e}")
            return redirect("signup")

        # fixed the issue of login() breaking down bc multiple authications so it was confused
        auth_user = authenticate(request, username=username, password=password)
        if auth_user is None:
            messages.info(request, "Account created. Please log in.")
            return redirect("login")

        login(request, auth_user)

        return redirect("/")

    return render(request, "signup.html")


def user_logout(request):
    logout(request)
    return redirect("/")
