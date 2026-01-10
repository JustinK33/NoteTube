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
            generate_content=note_content
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

def download_audio(link):
    yt = YouTube(link)
    video = yt.streams.filter(only_audio=True).first()
    if video is None:
        raise ValueError("No audio stream found for this video.")
    #im using pytubefix instead of pytube because of some bugs
    out_file = video.download(output_path=settings.MEDIA_ROOT)
    base, ext = os.path.splitext(out_file) # type: ignore
    new_file = base + '.mp3'
    os.rename(out_file, new_file) # type: ignore
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

    prompt = f"""You are a meticulous note-taking assistant. I will paste a YouTube video transcript. 
Your job is to turn it into a high-quality STUDY SHEET (not just a summary).

RULES
- Use ONLY information that appears in the transcript. If something is unclear or missing, write: [UNCLEAR IN TRANSCRIPT] and list what’s missing.
- Preserve key terms, definitions, steps, formulas, and examples exactly as intended.
- Write for a student who wants to learn + review quickly for a quiz/exam.
- Prefer clarity and structure over being short.

OUTPUT FORMAT (use these sections in this order)

1) Video at a Glance (5–10 bullets)
- The main learning goals and what you should be able to do after watching.

2) Key Concepts & Definitions
- A table with: Term | Definition (plain English) | Why it matters | Common confusion/mistake (if mentioned or implied).

3) Big Ideas Explained
- Short explanations (3–6 sentences each) of the most important ideas.
- Include “why” reasoning and intuition, not just what it is.

4) Step-by-Step Processes / Workflows
- If the transcript describes any procedure, algorithm, or method: convert it to numbered steps.
- Add “When to use this” and “How to check you did it right”.

5) Examples Walkthroughs
- Extract every example from the transcript and rewrite it cleanly.
- For each example: Problem/Prompt → Steps → Final result → Common mistake.

6) Cheat Sheet
- Key formulas, rules, or patterns in a compact list.
- If there are no formulas, list “If you see X → do Y” rules.

7) Quick Self-Quiz (with answers)
- 10–15 questions: mix of definitions, “explain why”, apply-the-steps, and a couple of trick questions based on common mistakes.
- Provide answers immediately below each question.

8) Transcript Anchors
- Provide 8–15 direct “anchor points” so I can find things again.
- Format: [Approx section / quote snippet] → what it teaches (keep quote snippets short).

Now wait for me to paste the transcript:\n\n{transcription}\n\nNotes:"""

    response = openai.completions.create(
        model="gpt-5-mini",
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

        if password == repeatPassword:
            try:
                user = User.objects.create_user(username, email, password)
                user.save()
                login(request, user)
                return redirect('login')
            except:
                messages.error(request, 'Error creating account')
                return redirect('signup')

        else:
            messages.error(request, "Passwords do not match")
            return redirect('signup')
        
    return render(request, 'signup.html')

def user_logout(request):
    logout(request)
    return redirect('/')
