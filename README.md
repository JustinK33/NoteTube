# NoteTube

[![CI](https://github.com/JustinK33/NoteTube/actions/workflows/ci.yml/badge.svg)](https://github.com/JustinK33/NoteTube/actions/workflows/ci.yml)

A web app that turns YouTube videos and audio recordings into structured, searchable notes.

## What it does

The hard part of this project was never the note generation, it was getting a transcript at all.
YouTube blocks datacenter IPs, so the moment this moved from my laptop to an EC2 box the transcript fetch started returning 403s, 429s, and CAPTCHA pages.
Most of the interesting engineering here is the fallback chain and the async plumbing that grew out of that.

Paste a YouTube link or upload an MP3 and NoteTube transcribes it and generates structured notes in the background, so you get a response in under a second and poll for the result rather than watching a spinner for two minutes.
Once you have a library of notes, you can ask questions across all of them with semantic search, and export any note to TXT, Markdown, PDF, or straight into Notion.
Login is email/password or Google.

Transcript fetching tries SerpAPI's `youtube_transcript` engine first, falls back to downloading the audio with yt-dlp and transcribing it through AssemblyAI, and if both fail it returns a 502 with an error code the frontend translates into "upload the MP3 instead".
Successful transcripts cache in Redis for an hour and failures for ten minutes, so a blocked video doesn't cost a fresh round trip on every retry.

## Tech stack

| Layer | What it uses |
| --- | --- |
| Web | Django, Django REST Framework, django-allauth, Gunicorn, Tailwind CSS |
| Async | Celery with a Redis broker |
| Second service | gRPC and Protocol Buffers (`content-service`) |
| AI | OpenAI GPT-4.1-nano for note generation, GPT-4o-mini for questions, text-embedding-3-small for vectors, AssemblyAI for speech to text |
| Retrieval | LangChain, pgvector, PostgreSQL |
| Retrieval quality | LLM grades every retrieval, rewrites the query and retries up to twice, records the outcome for measurement |
| Transcripts | SerpAPI, yt-dlp, pytubefix |
| Export | reportlab for PDF, Notion API |
| Infra | Docker Compose, nginx, Certbot, AWS EC2, Vercel for the slim frontend |
| Checks | pytest, pytest-django, pytest-cov, Black |

## Architecture

```mermaid
flowchart TD
    B[Browser] -->|"POST /generate-notes<br>POST /api/notes/search/"| N[nginx]
    N -->|"HTTP, internal"| D["Django + DRF<br>Gunicorn, 3 workers"]
    D -->|"202 Accepted, task_id"| B
    B -->|"GET /api/notes/search/:task_id/"| N
    D -->|enqueue| R[("Redis<br>broker, cache, semantic cache")]
    R --> W[Celery worker]
    W -->|"SerpAPI, then yt-dlp + AssemblyAI"| T[Transcript sources]
    W -->|"note_generator.generate_note"| G["content-service<br>gRPC, NoteSection"]
    W -->|"note_generator.search_notes"| L["Grading loop<br>max 3 passes"]
    L -->|"cosine top-5 on langchain_pg_embedding"| P[("PostgreSQL<br>+ pgvector")]
    L -->|"grade, rewrite, then answer"| O["OpenAI<br>gpt-4o-mini"]
    W -->|"NotePost, NoteEmbedding, RetrievalAttempt"| P
```

Ask a question and Django validates it, hands it to Celery, and answers `202` with a task id in a few milliseconds while the browser starts polling.
The worker embeds the question, pulls the five nearest notes out of pgvector, and asks the model whether those notes can actually answer it; a verdict of `INSUFFICIENT` comes back with a rewritten query, the search runs again, and that repeats at most twice more.
Every question writes one `RetrievalAttempt` row with the grades, the rewrites, and the outcome, and if all three passes came back insufficient the answer is still generated but flagged `low_confidence` so the UI can say so rather than presenting a guess as fact.

Six Docker Compose services on one EC2 instance.
A request comes in over HTTPS, nginx terminates TLS and serves static files itself, and Django hands anything slow to Celery over Redis and returns a task id immediately.
The worker fetches the transcript, calls the `content-service` over gRPC to chunk it into `NoteSection` messages, sends those through OpenAI for the note text and again for embeddings, and writes the `NotePost` plus its vectors into Postgres.
Redis does triple duty as the Celery broker, the transcript cache, and the RAG semantic cache; Certbot renews the certificate on a 12 hour loop and reloads nginx without dropping connections.

## What building this taught me

**Your IP address is part of your architecture.**
Everything worked locally and broke on EC2 because YouTube blocks datacenter ranges, and I spent a while treating that as a bug in my code before accepting it was a property of where the code ran.
The fix was a whole fallback chain instead of one change: a typed error hierarchy, a SerpAPI path that fetches from outside my IP, a yt-dlp plus AssemblyAI path that pays money rather than getting blocked, and a `diagnose_transcript` command that says which layer failed.

**Caching the failures mattered more than caching the successes.**
Successful transcripts cache for an hour and failed ones for ten minutes.
Without the failure cache, a user hammering retry on a blocked video generated a fresh outbound request every time, which is the exact pattern that gets you blocked harder.

**Splitting a service for latency paid off as a typed contract instead.**
`content-service` moved out to gRPC so chunking would not block a Gunicorn worker, and the real prize was `proto/content_service.proto`.
The boundary between Django and the chunker is now a schema both sides compile against, so a shape change is a build error rather than a `KeyError` in a Celery task at 2am.

## Documentation

- [PROJECT.md](PROJECT.md) is the long-form writeup: the full service diagram, the pipeline stage by stage, and the deployment layout.
- [proto/content_service.proto](proto/content_service.proto) is the gRPC contract between Django and the content-service.
- [.env.example](.env.example) lists every environment variable with a note on what it's for.
- `python manage.py retrieval_stats --days 30` reports how often retrieval was graded insufficient and whether a rewritten query recovered it, with raw counts next to every percentage.
- `python manage.py diagnose_transcript <url>` walks the transcript fallback chain one layer at a time and says which one failed.

## Quick start

```bash
git clone https://github.com/JustinK33/NoteTube.git
cd NoteTube
cp .env.example .env   # fill in the API keys
docker compose up --build
```

The app comes up on `http://localhost:8000`.

`.env.example` documents all of them, but the ones you cannot skip are `SECRET_KEY`, `OPENAI_API_KEY`, `APIKEY` for AssemblyAI, the `PG*` connection settings, and `REDIS_URL`.
`SERP_API`, the Google OAuth pair, and `YTDLP_COOKIES_FILE` are optional, and without `SERP_API` transcript fetching goes straight to the yt-dlp and AssemblyAI path.

Tests run against SQLite in memory, so they need no services up:

```bash
pip install -r requirements-dev.txt
pytest tests/ --cov=Backend/note_generator
```

## License

MIT, see LICENSE.
