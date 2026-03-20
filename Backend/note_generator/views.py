from django.contrib.auth import authenticate, login, logout
from django.shortcuts import render, redirect
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.views.decorators.csrf import csrf_exempt
from django.http import JsonResponse, HttpResponse
from django.conf import settings
from fastapi import HTTPException
import json, os, time
from pytubefix import YouTube
import assemblyai as aai
import openai
from .models import NotePost
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
    if request.method == "POST":
        try:
            # Get MP3 file and title from form data
            mp3_file = request.FILES.get("mp3_file")
            title = request.POST.get("title", "Untitled Note")

            if not mp3_file:
                return JsonResponse({"error": "No MP3 file provided"}, status=400)

            if not mp3_file.name.endswith(".mp3"):
                return JsonResponse({"error": "File must be an MP3"}, status=400)

            # Save MP3 to temporary location
            temp_dir = tempfile.mkdtemp()
            mp3_path = os.path.join(temp_dir, mp3_file.name)
            with open(mp3_path, "wb") as f:
                for chunk in mp3_file.chunks():
                    f.write(chunk)

            # Get transcript from MP3
            transcription = get_mp3_transcript(mp3_path)
            if not transcription:
                shutil.rmtree(temp_dir)
                return JsonResponse({"error": "Failed to get transcript"}, status=500)

            # Use OpenAI to generate notes
            note_content = generate_blog_from_transcription(transcription)
            if not note_content:
                shutil.rmtree(temp_dir)
                return JsonResponse({"error": "Failed to generate notes"}, status=500)

            # Save notes to database
            new_note = NotePost.objects.create(
                user=request.user,
                youtube_title=title,
                youtube_link="",  # No YouTube link for MP3
                generated_content=note_content,
            )
            new_note.save()

            # Clean up temporary files
            shutil.rmtree(temp_dir)

            # Invalidate cached list for this user
            safe_cache_delete(f"notes:list:user:{request.user.id}")

            # Return generated notes as response
            return JsonResponse({"content": note_content})

        except Exception as e:
            return JsonResponse(
                {"error": f"Error processing MP3: {str(e)}"}, status=500
            )
    else:
        return JsonResponse({"error": "Invalid request method"}, status=405)


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

    # Rate limiting: 10 minutes between requests
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
                {
                    "error_code": "invalid_url",
                    "message": str(e),
                },
                status=400,
            )
    except (KeyError, json.JSONDecodeError):
        return JsonResponse(
            {"error_code": "invalid_request", "message": "Invalid data sent"},
            status=400,
        )

    # Get title (non-critical)
    try:
        title = yt_title(yt_link)
    except Exception as e:
        logger.warning(f"Could not fetch title for {yt_link}: {e}")
        title = f"YouTube Note ({yt_link[:50]})"

    # Get transcript with caching and error handling
    from note_generator.transcript_utils import get_transcript_with_diagnostics

    transcript, transcript_error = get_transcript_with_diagnostics(
        yt_link, get_transcript
    )

    if transcript_error:
        return JsonResponse(
            {
                "error_code": transcript_error.error_code,
                "message": transcript_error.message,
            },
            status=transcript_error.http_status,
        )

    if not transcript:
        return JsonResponse(
            {
                "error_code": "no_transcript",
                "message": "Transcript unavailable. Try MP3 upload or paste transcript.",
            },
            status=502,
        )

    # Generate notes
    try:
        note_content = generate_blog_from_transcription(transcript)
        if not note_content:
            return JsonResponse(
                {
                    "error_code": "generation_failed",
                    "message": "Failed to generate notes. Please try again.",
                },
                status=500,
            )
    except Exception as e:
        logger.exception(f"Note generation failed: {e}")
        return JsonResponse(
            {
                "error_code": "generation_failed",
                "message": "Note generation service temporarily unavailable.",
            },
            status=503,
        )

    # Save to database
    try:
        new_note = NotePost.objects.create(
            user=request.user,
            youtube_title=title,
            youtube_link=yt_link,
            generated_content=note_content,
        )
        new_note.save()

        # Invalidate cache
        safe_cache_delete(f"notes:list:user:{request.user.id}")

        # Set rate limit (10 minutes)
        safe_cache_set(rate_limit_key, time.time(), timeout=600)

        return JsonResponse({"content": note_content})
    except Exception as e:
        logger.exception(f"Failed to save note: {e}")
        return JsonResponse(
            {
                "error_code": "save_failed",
                "message": "Could not save notes. Please try again.",
            },
            status=500,
        )


@login_required
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
def note_details(request, pk):
    key = f"notes:detail:user:{request.user.id}:pk:{pk}"

    try:
        note_post_detail = cached_get_or_set(
            key=key,
            timeout=300,
            compute=lambda: NotePost.objects.get(id=pk, user=request.user),
        )
    except NotePost.DoesNotExist:
        return redirect("/note-list")

    return render(request, "note_details.html", {"note_post_detail": note_post_detail})


@login_required
def note_edit(request, pk):
    try:
        note_post = NotePost.objects.get(id=pk, user=request.user)
    except NotePost.DoesNotExist:
        return redirect("saved-notes")

    if request.method == "POST":
        new_title = request.POST.get("youtube_title", "").strip() or note_post.youtube_title
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
