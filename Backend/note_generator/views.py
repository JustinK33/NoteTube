from django.contrib.auth.models import User
from django.contrib.auth import authenticate, login, logout
from django.shortcuts import render, redirect
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.views.decorators.csrf import csrf_exempt
from django.http import JsonResponse
from django.conf import settings
import json, os
from pytubefix import YouTube
import assemblyai as aai
import openai
from .models import NotePost
import traceback
import tempfile

def home(request):
    return render(request, 'home.html')

@login_required
def index(request):
    return render(request, 'index.html')

@csrf_exempt
def generate_note(request):
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            yt_link = data['link']
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

        # return ai made notes as a response
        return JsonResponse({"content": note_content})

    else:
        return JsonResponse({'error': 'Invalid request method'}, status=405)

def note_list(request):
    notetube_post = NotePost.objects.filter(user=request.user)
    return render(request, "notes.html", {'notetube_post': notetube_post})

def note_details(request, pk):
    note_post_detail = NotePost.objects.get(id=pk)
    if request.user == note_post_detail.user:
        return render(request, 'note_details.html', {'note_post_detail': note_post_detail})
    else:
        return redirect('/')

def yt_title(link):
    yt = YouTube(link)
    title = yt.title
    return title

def download_audio(link): # made this download temp only for transcription then delete
    yt = YouTube(link)
    video = yt.streams.filter(only_audio=True).first()
    if video is None:
        raise ValueError("No audio stream found for this video.")
    
    #im using pytubefix instead of pytube because of some bugs
    out_file = tempfile.gettempdir()
    downloaded_path = video.download(output_path=out_file)

    base, ext = os.path.splitext(downloaded_path) # type: ignore
    new_file = base + '.mp3'
    os.rename(downloaded_path, new_file) # type: ignore
    return new_file

# we gon use assembly ai to get the transcription
def get_transcript(link):
    audio_file = download_audio(link)
    aai.settings.api_key = os.getenv('APIKEY')

    transcriber = aai.Transcriber()
    transcript = transcriber.transcribe(audio_file)

    return transcript.text

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
