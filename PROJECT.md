# NoteTube

> AI-powered note generation from YouTube videos and audio files - with semantic search, async processing, and Notion export.

---

## What It Does

NoteTube turns YouTube videos and MP3 recordings into structured, exam-ready notes using a multi-stage AI pipeline. Users paste a link, and within seconds the system has fetched the transcript, chunked and processed it through a custom gRPC microservice, fallen back to OpenAI when needed, embedded the result into a vector store, and made it searchable via natural language queries.

---

## Architecture

```
┌──────────────────────────────────────────────────────┐
│                       Internet                        │
└────────────────────────┬─────────────────────────────┘
                         │ HTTPS
                    ┌────▼────┐
                    │  nginx  │  TLS termination, static file serving
                    │(alpine) │  Let's Encrypt / Certbot auto-renew
                    └────┬────┘
                         │ HTTP (internal)
              ┌──────────▼──────────┐
              │   web (Django 6)    │  Gunicorn, 3 workers
              │   + REST API (DRF)  │  Session auth + Google OAuth
              │   + allauth         │  Rate limiting, cache layer
              └──────┬──────┬───────┘
                     │      │
         ┌───────────▼──┐  ┌▼─────────────────┐
         │ celery-worker │  │  content-service  │
         │  (2 workers)  │  │  (gRPC / Python)  │
         │               │  │  Transcript →     │
         │  - YouTube    │  │  chunking →       │
         │    transcript │  │  structured notes │
         │  - AssemblyAI │  └──────────────────-┘
         │  - OpenAI     │
         │  - Embeddings │
         │  - Notion API │
         └───────┬───────┘
                 │
        ┌────────▼────────┐
        │      Redis      │  Task broker + result backend
        │   (redis:7)     │  Cache layer (django-redis)
        │                 │  RAG semantic cache (LangChain)
        └────────┬────────┘
                 │
        ┌────────▼────────┐
        │   PostgreSQL    │  Primary database
        │  + pgvector     │  Vector store for RAG embeddings
        └─────────────────┘
```

<p align="center">
  <img src="docs/NoteTube.png" alt="NoteTube system architecture" width="800"/>
</p>
<p align="center"><em>The same layout drawn out: nginx to Django, to the Celery workers and the gRPC content-service, down to Postgres/pgvector and Redis. Predates the async search path described below.</em></p>

---

## Tech Stack

| Layer | Technology |
|---|---|
| **Language** | Python 3.12 |
| **Web framework** | Django 6, Django REST Framework |
| **Task queue** | Celery + Redis (async note generation, MP3, embeddings, Notion) |
| **gRPC microservice** | Python grpcio - transcript processing pipeline |
| **AI / LLM** | OpenAI GPT-4.1-nano (notes), text-embedding-3-small (RAG) |
| **Speech-to-text** | AssemblyAI (MP3 → transcript fallback) |
| **YouTube** | SerpAPI `youtube_transcript` engine (captions), yt-dlp (audio download fallback) |
| **Vector search** | pgvector + LangChain (semantic note search) |
| **Cache** | Redis via django-redis (transcript cache, rate limits, RAG semantic cache) |
| **Auth** | Django allauth + Google OAuth2 |
| **Database** | PostgreSQL (AWS RDS / Neon) |
| **Reverse proxy** | nginx (alpine) |
| **TLS** | Let's Encrypt via Certbot (auto-renew every 12h) |
| **Containerisation** | Docker Compose (6 services) |
| **Deployment** | AWS EC2 |
| **Export** | Notion API, PDF (ReportLab), Markdown, plain text |

---

## Services (Docker Compose)

| Service | Image | Role |
|---|---|---|
| `web` | custom (python:3.12-slim + ffmpeg) | Django API + Gunicorn |
| `celery-worker` | same image | Async task worker (concurrency=2) |
| `content-service` | same image | gRPC note-processing microservice |
| `redis` | redis:7-alpine | Broker, result backend, cache |
| `nginx` | nginx:alpine | Reverse proxy, TLS, static files |
| `certbot` | certbot/certbot | TLS certificate renewal |

---

## Feature Set

| Feature | Status |
|---|---|
| YouTube video → AI notes | ✅ Async (Celery) |
| MP3 upload → AI notes | ✅ Async (Celery) |
| Manual note creation | ✅ |
| Semantic search (RAG) | ✅ Async (Celery), pgvector + LangChain + GPT-4o-mini |
| Self-correcting retrieval | ✅ LLM grades each pass, rewrites and retries up to 2× |
| Retrieval telemetry | ✅ `RetrievalAttempt` table + `retrieval_stats` command |
| Vector embeddings on save | ✅ Async (Celery, retries 3×) |
| Notion page export | ✅ Async (Celery) |
| TXT / MD / PDF export | ✅ |
| Google OAuth login | ✅ |
| Rate limiting | ✅ Redis-backed per-user |
| Transcript caching | ✅ Redis (1h TTL) |
| RAG semantic cache | ✅ Redis (cosine similarity threshold 0.05) |
| TLS auto-renewal | ✅ Certbot every 12h |

---

## Performance

### Before Celery (synchronous)
| Operation | User wait time |
|---|---|
| YouTube note generation | 30 – 150 seconds |
| MP3 note generation | 10 – 60 seconds |
| Notion export | 5 – 15 seconds |
| Note save (embedding) | 5 – 15 seconds |

### After Celery (async)
| Operation | User wait time |
|---|---|
| Any note generation (enqueue) | < 1 second |
| Any note generation (complete) | 30 – 150s (background) |
| Notion export (enqueue) | < 1 second |
| Note save (embedding) | < 1 second |
| Polling endpoint (`/api/task-status/`) | < 50ms |

---

## AI / ML Pipeline

```
YouTube URL or MP3
       │
       ▼
  Transcript fetch (views.get_transcript)
  ├── SerpAPI youtube_transcript engine (~1s, fetches from outside our IP)
  └── Fallback: yt-dlp audio download → AssemblyAI STT (~30-90s)
       │
       ▼
  Transcript cached in Redis (1h TTL)
       │
       ▼
  gRPC → content-service
  ├── Text chunking (180-word windows)
  ├── Sentence extraction
  ├── Structured NoteSection protobuf response
  └── Fallback: OpenAI GPT-4.1-nano (direct completion)
       │
       ▼
  NotePost saved to PostgreSQL
       │
       ▼
  Async: OpenAI text-embedding-3-small → pgvector upsert
       │
       ▼
  RAG retrieval (on search, async via note_generator.search_notes):
  ├── User question → embedding → cosine similarity top-5
  ├── Grade the retrieved notes: SUFFICIENT or INSUFFICIENT
  │   ├── INSUFFICIENT → grader also returns a rewritten query
  │   ├── Re-search with the rewrite and grade again (max 2 retries, 3 passes)
  │   └── Unparseable grade → treated as sufficient, recorded separately
  ├── Redis semantic cache (threshold 0.05), shared by grader and answer calls
  ├── GPT-4o-mini → answer with citations
  ├── All 3 passes insufficient → caveated answer + low_confidence=True
  └── One RetrievalAttempt row written per question
```

The grader always sees the original question, never a rewrite, so every pass is judged against what the user actually asked.

One caveat on how far this can go: `rag/embed.py` stores one vector per whole `NotePost`, not per transcript chunk.
A rewritten query can therefore re-rank whole notes but cannot surface a better passage inside a note that was already retrieved.
If `retrieval_stats` shows retries rarely recovering, chunking is the fix, not a smarter grader.

---

## API Endpoints

| Method | Path | Description | Auth |
|---|---|---|---|
| `GET` | `/` | Landing page | No |
| `GET` | `/index` | Dashboard | Yes |
| `POST` | `/generate-notes` | Enqueue YouTube note task | Yes |
| `POST` | `/mp3-to-notes` | Enqueue MP3 note task | Yes |
| `GET` | `/api/task-status/<id>/` | Poll async task result | Yes |
| `POST` | `/api/notes/search/` | Enqueue a graded RAG search, returns `202` + `task_id` | Yes |
| `GET` | `/api/notes/search/<task_id>/` | Poll the search: `pending`, `processing`, `done`, `failed`; `done` carries `answer`, `sources`, `low_confidence` | Yes |
| `GET` | `/saved-notes` | List all notes (cached 60s) | Yes |
| `GET` | `/note-details/<pk>/` | View note (cached 300s) | Yes |
| `GET/POST` | `/note-edit/<pk>/` | Edit note | Yes |
| `POST` | `/note-delete/<pk>/` | Delete note | Yes |
| `GET` | `/note-export/<pk>/` | Download TXT / MD / PDF | Yes |
| `POST` | `/note-export-notion/<pk>/` | Enqueue Notion export task | Yes |
| `GET/POST` | `/notion-settings` | Configure Notion integration | Yes |
| `GET/POST` | `/note-create` | Create manual note | Yes |

---

## Data Models

### `NotePost`
| Field | Type | Description |
|---|---|---|
| `user` | FK(User) | Owner |
| `youtube_title` | CharField(300) | Note title |
| `youtube_link` | URLField (nullable) | Source video |
| `generated_content` | TextField | AI-generated notes |
| `created_at` | DateTimeField | Auto-set on creation |

### `UserProfile`
| Field | Type | Description |
|---|---|---|
| `user` | OneToOneField | Django user |
| `notion_token` | CharField(255) | Notion integration token |
| `notion_parent_page_id` | CharField(64) | Notion destination page |

### `NoteEmbedding`
| Field | Type | Description |
|---|---|---|
| `note` | OneToOneField(NotePost) | Linked note |
| `vector_id` | CharField(64, unique) | pgvector document ID |
| `content_hash` | CharField(64) | SHA-256 of content (skip re-embed if unchanged) |
| `updated_at` | DateTimeField | Auto-updated |

### `RetrievalAttempt`

One row per question asked, written by `note_generator.search_notes`. This table is the measurement, so it is read-only in the admin and a failed write is logged rather than raised, since losing telemetry must never cost a good answer.

| Field | Type | Description |
|---|---|---|
| `user` | FK(User), `SET_NULL` | Asker; nulled rather than deleted so the sample does not shrink |
| `query` | TextField | The question as the user typed it |
| `attempts` | PositiveSmallInteger | Retrieval passes used, 1 to 3 |
| `first_grade` | CharField, indexed | `SUFFICIENT` / `INSUFFICIENT` / `UNPARSEABLE`; duplicates `grades[0]` because every stat groups by it |
| `grades` | JSONField | One grade per pass, e.g. `["INSUFFICIENT", "SUFFICIENT"]` |
| `rewritten_queries` | JSONField | Rewrites that were actually searched with |
| `outcome` | CharField, indexed | `sufficient_first_pass` / `sufficient_after_retry` / `exhausted` |
| `first_doc_count` | PositiveSmallInteger | Notes returned by the first pass |
| `final_doc_count` | PositiveSmallInteger | Notes the answer was generated from |
| `latency_ms` | PositiveInteger | Wall clock for the whole loop; includes semantic cache hits, so not raw model cost |
| `created_at` | DateTimeField, indexed | Auto-set on creation |

---

## Deployment

**Infrastructure**: AWS EC2 (single instance), Docker Compose

**TLS**: Let's Encrypt certificates auto-renewed every 12 hours via Certbot. nginx polls for a `.renewed` sentinel file and reloads without downtime.

**Static files**: Collected to `/vol/static` by Django (`collectstatic`) and served directly by nginx - bypasses gunicorn entirely.

**Database**: External PostgreSQL with `sslmode=require`. pgvector extension enabled for vector similarity search.

---

## Local Development

```bash
git clone <repo>
cd NoteTube

# Create .env from template (fill in API keys)
cp .env.example .env

# Build and start all 6 services
docker compose up --build

# App available at http://localhost:8000
```

**Required environment variables:**

| Variable | Description |
|---|---|
| `SECRET_KEY` | Django secret key |
| `OPENAI_API_KEY` | OpenAI API key (notes + embeddings) |
| `APIKEY` | AssemblyAI API key (audio transcription) |
| `PGDATABASE / PGUSER / PGPASSWORD / PGHOST / PGPORT` | PostgreSQL connection |
| `REDIS_URL` | Redis URL (overridden to local Redis in Docker) |
| `GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET` | Google OAuth (optional) |
| `DEBUG` | `true` for local dev, `false` in production |
| `ALLOWED_HOSTS` | Comma-separated hostnames |
| `CSRF_TRUSTED_ORIGINS` | Trusted origins for CSRF |
| `YTDLP_COOKIES_FILE` | Path to YouTube cookies file (improves video access) |
