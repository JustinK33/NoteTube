from django.contrib.auth import authenticate, login, logout
from django.shortcuts import render, redirect
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.views.decorators.csrf import csrf_exempt
from django.http import JsonResponse
from django.conf import settings
from fastapi import HTTPException
import json, os
from pytubefix import YouTube
import assemblyai as aai
import openai
from .models import NotePost
import traceback
import tempfile
from note_generator.utils.cache_utils import cached_get_or_set
from django.core.cache import cache
import re
import logging
import subprocess
from fastapi.responses import FileResponse
import tempfile
import shutil

aai.settings.api_key = os.getenv('APIKEY')

def home(request):
    return render(request, 'home.html')

@login_required
def index(request):
    return render(request, 'index.html')

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
    if request.method == 'POST':
        try:
            # Get MP3 file and title from form data
            mp3_file = request.FILES.get('mp3_file')
            title = request.POST.get('title', 'Untitled Note')
            
            if not mp3_file:
                return JsonResponse({'error': 'No MP3 file provided'}, status=400)
            
            if not mp3_file.name.endswith('.mp3'):
                return JsonResponse({'error': 'File must be an MP3'}, status=400)
            
            # Save MP3 to temporary location
            temp_dir = tempfile.mkdtemp()
            mp3_path = os.path.join(temp_dir, mp3_file.name)
            with open(mp3_path, 'wb') as f:
                for chunk in mp3_file.chunks():
                    f.write(chunk)
            
            # Get transcript from MP3
            transcription = get_mp3_transcript(mp3_path)
            if not transcription:
                shutil.rmtree(temp_dir)
                return JsonResponse({'error': 'Failed to get transcript'}, status=500)
            
            # Use OpenAI to generate notes
            note_content = generate_blog_from_transcription(transcription)
            if not note_content:
                shutil.rmtree(temp_dir)
                return JsonResponse({'error': 'Failed to generate notes'}, status=500)
            
            # Save notes to database
            new_note = NotePost.objects.create(
                user=request.user,
                youtube_title=title,
                youtube_link='',  # No YouTube link for MP3
                generated_content=note_content
            )
            new_note.save()
            
            # Clean up temporary files
            shutil.rmtree(temp_dir)
            
            # Invalidate cached list for this user
            cache.delete(f"notes:list:user:{request.user.id}")
            
            # Return generated notes as response
            return JsonResponse({"content": note_content})
        
        except Exception as e:
            return JsonResponse({'error': f'Error processing MP3: {str(e)}'}, status=500)
    else:
        return JsonResponse({'error': 'Invalid request method'}, status=405)

def get_mp3_transcript(mp3_path: str) -> str:
# since they upload a mp3 we dont need to store it and just get a transcript from it
    if not mp3_path.endswith(".mp3"):
        raise ValueError("File must be an .mp3")
    
    if not os.path.exists(mp3_path):
        raise FileNotFoundError(f"{mp3_path} not found")
    
    transcriber = aai.Transcriber()
    transcript = transcriber.transcribe(mp3_path)

    if transcript.text is None: # fixes type check error
        raise RuntimeError("Transcription error: no text returned")

    return transcript.text


@login_required
@csrf_exempt
def generate_note(request):
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            yt_link_raw = data.get("link", "")
            try:
                yt_link = normalize_youtube_url(yt_link_raw)
            except ValueError as e:
                return JsonResponse({"error": str(e)}, status=400)
        except (KeyError, json.JSONDecodeError):
            return JsonResponse({'error': 'Invalid data sent'}, status=400)

        # get yt title
        title = yt_title(yt_link)

        # get transcript
        transcription = get_transcript(yt_link)
        if not transcription:
            return JsonResponse({'error': " Failed to get transcript"}, status=500)

        # use OpenAI to generate the ai notes
        note_content = generate_blog_from_transcription(transcription)
        if not note_content:
            return JsonResponse({'error': " Failed to generate blog article"}, status=500)

        # save notes to db
        new_note = NotePost.objects.create(
            user=request.user,
            youtube_title=title,
            youtube_link=yt_link,
            generated_content=note_content
        )
        new_note.save()

        # invalidate cahced list for this user 
        cache.delete(f"notes:list:user:{request.user.id}")

        # return ai made notes as a response
        return JsonResponse({"content": note_content})

    else:
        return JsonResponse({'error': 'Invalid request method'}, status=405)

@login_required
def note_list(request):
    key = f"notes:list:user:{request.user.id}"

    notetube_post = cached_get_or_set(key=key, timeout=60, compute=lambda: list(NotePost.objects.filter(user=request.user).order_by("-id")))
    return render(request, "notes.html", {'notetube_post': notetube_post})

@login_required
def note_details(request, pk):
    key = f"notes:detail:user:{request.user.id}:pk:{pk}"

    try:
        note_post_detail = cached_get_or_set(key=key, timeout=300, compute=lambda: NotePost.objects.get(id=pk, user=request.user))
    except NotePost.DoesNotExist:
        return redirect('/note-list')

    return render(request, 'note_details.html', {'note_post_detail': note_post_detail})

def yt_title(link):
    yt = YouTube(link)
    title = yt.title
    return title

import subprocess, tempfile, os, glob

def download_audio(link: str) -> str:
    tmpdir = tempfile.gettempdir()
    outtmpl = os.path.join(tmpdir, "%(id)s.%(ext)s")

    # downloads best audio; yt-dlp chooses an audio container (webm/m4a usually)
    cmd = [
        "yt-dlp",
        "-f", "bestaudio",
        "-o", outtmpl,
        link,
    ]

    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"yt-dlp failed:\nSTDOUT:\n{res.stdout}\nSTDERR:\n{res.stderr}")

    # find the newest downloaded file in /tmp (simple + reliable)
    candidates = sorted(glob.glob(os.path.join(tmpdir, "*.*")), key=os.path.getmtime, reverse=True)
    if not candidates:
        raise RuntimeError("yt-dlp succeeded but no file found in temp dir")

    return candidates[0]

# we gon use assembly ai to get the transcription
def get_transcript(link):
    audio_file = download_audio(link)
    try:
        transcriber = aai.Transcriber()
        transcript = transcriber.transcribe(audio_file)
        return transcript.text
    finally:
        if audio_file and os.path.exists(audio_file):
            os.remove(audio_file)

def generate_blog_from_transcription(transcription):
    openai.api_key = os.getenv('OPENAI_API_KEY')

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
        model="gpt-4.1-nano",
        prompt=prompt,
        max_tokens=1000
    )
    generated_content = response.choices[0].text.strip()
    return generated_content
    
def user_login(request):
    if request.method == "POST":
        username = request.POST['username']
        password = request.POST['password']

        user = authenticate(request, username=username, password=password)
        if user is not None:
            login(request, user)
            return redirect('/index')
        else:
            # Use POST-Redirect-GET pattern to avoid browser "resubmit form" warnings
            messages.error(request, "Invalid username or password")
            return redirect('login')
    return render(request, 'login.html')

def user_signup(request):
    if request.method == "POST":
        username = request.POST['username']
        email = request.POST['email']
        password = request.POST['password']
        repeatPassword = request.POST['repeatPassword']

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

        
    return render(request, 'signup.html')

def user_logout(request):
    logout(request)
    return redirect('/')
