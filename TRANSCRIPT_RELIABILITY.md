# Production-Ready Transcript Reliability Guide

## Problem Summary

In production (AWS EC2), YouTube often blocks transcript fetching with:
- **HTTP 403 (Forbidden)** - IP is blocked
- **HTTP 429 (Too Many Requests)** - Rate limited
- **CAPTCHA** - Treated as bot
- **No captions** - Video has no subtitles

The frontend would show generic "Something went wrong" error, leaving users confused.

## Solution Architecture

### 1. **New Error Handling Layer** (`transcript_utils.py`)

#### Key Classes:
```python
class TranscriptFetchError(Exception):
    """Base exception with error_code, message, and http_status"""
    
class YouTubeBlockedError(TranscriptFetchError):
    """Handles 403/429/CAPTCHA errors"""
    
class NoTranscriptError(TranscriptFetchError):
    """Handles videos without captions"""
```

#### Key Function:
```python
get_transcript_with_diagnostics(youtube_url, get_transcript_func) -> (str|None, TranscriptFetchError|None)
```

**Why this works:**
- Returns **structured error info** (error_code + message) instead of generic messages
- Detects specific error types (403, 429, CAPTCHA) and provides helpful hints
- Catches exceptions at the boundary between YouTube API and business logic
- **Always returns a tuple** - caller must check for errors explicitly

### 2. **Updated `generate_note` View** (`views.py`)

**Old behavior:**
```python
transcription = get_transcript(yt_link)
if not transcription:
    return JsonResponse({"error": "Failed to get transcript"}, status=500)
```
Problem: Doesn't distinguish between different failure modes, always 500, doesn't help user.

**New behavior:**
```python
transcript, transcript_error = get_transcript_with_diagnostics(yt_link, get_transcript)

if transcript_error:
    return JsonResponse({
        "error_code": transcript_error.error_code,  # "youtube_blocked", "no_transcript", etc.
        "message": transcript_error.message,         # User-friendly hint
    }, status=transcript_error.http_status)          # 502 (Bad Gateway) for YouTube issues
```

**Why this works:**
- **error_code** allows frontend to show context-specific UI (e.g., "Try MP3 tab")
- **502 Bad Gateway** (instead of 500) tells load balancers/proxies that it's upstream issue
- Logs actual exception via `transcript_error` for debugging

### 3. **Frontend Error Handling** (`index.html`)

**Old behavior:**
```javascript
if (data.error) {
    alert('Error: ' + data.error);
}
```
Problem: Alert boxes are jarring, don't help users know to try MP3.

**New behavior:**
```javascript
if (data.error_code) {
    let userMessage = data.message;
    if (errorCode === 'youtube_blocked') {
        userMessage += '\n\n💡 Tip: Try uploading an MP3 file instead...';
    }
    blogContent.innerHTML = `<p>${userMessage}</p>`;
}
```

**Why this works:**
- Shows error **in-place** on the page (not alert box)
- Suggests **alternative workflow** (MP3 upload) right in the error message
- User doesn't get stuck wondering what to do next

### 4. **Transcript Caching** (`transcript_utils.py`)

```python
cache_key = f"transcript:video:{video_id}"

# Check cache first (works for both success + failure)
cached = cache.get(cache_key)
if cached:
    return cached_value, None

# If not cached, fetch and cache
try:
    transcript = get_transcript_func(youtube_url)
    cache.set(cache_key, transcript, timeout=3600)  # Cache 1 hour on success
except Exception as e:
    # Cache error for 10 minutes to avoid hammering YouTube
    cache.set(cache_key, {"is_error": True, "error": {...}}, timeout=600)
    return None, error
```

**Why this works:**
- **Success caching (1 hour)** - Reduces requests to YouTube, speeds up page loads
- **Failure caching (10 minutes)** - Prevents retry storms when rate limited
- **Uses video_id** as cache key - Same video, same cache (benefits many users)
- **Distinguishes success from failure** - Different timeout strategies

### 5. **Diagnostics Management Command** (`diagnose_transcript.py`)

Run on EC2 to identify the root cause:

```bash
python manage.py diagnose_transcript "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
```

**Output:**
```
✓ Video ID extracted: dQw4w9WgXcQ
✗ Fetch failed: HTTPError
  Details: HTTP 403: Forbidden

💡 HTTP 403 (Forbidden): YouTube is blocking this IP.
   Solution: Use a proxy/VPN or check AWS IP restrictions.
```

**Why this works:**
- Operator can quickly identify if it's 403, 429, CAPTCHA, or network issue
- Provides specific remediation steps (unlike generic error messages)
- **Runnable on-demand** - No need to recreate error in production

---

## HTTP Status Codes Explained

| Code | Scenario | Why | What User Sees |
|------|----------|-----|-----------------|
| **400** | Invalid URL | Client error, won't fix | "Invalid YouTube link" |
| **500** | Save failed, OpenAI down | Server error, may be temporary | "Service unavailable" (suggests retry) |
| **502** | YouTube blocked/403/429 | Upstream service blocking | "YouTube blocked. Try MP3 upload." |
| **503** | Note generation failed | Service temporarily unavailable | "Generation service temporarily down" |

**Key point:** 502 vs 500 tells monitoring systems to **not retry immediately** (it's upstream issue).

---

## Cache Behavior

### Success Path:
```
Request 1: Video ID extracted → Fetch from YouTube → Cache 1hr → Return
Request 2 (within 1hr): Cache hit → Return immediately ⚡
Request 3 (after 1hr): Cache expired → Fetch again
```

### Failure Path (Rate Limited):
```
Request 1: Rate limited (429) → Cache error 10min → Return "blocked" error
Request 2 (within 10min): Cached error → Return immediately (no fetch!) ⚡
Request 3 (after 10min): Cache expired → Retry fetch
```

**Why this works:** Rate limit errors have a time component. Caching them for 10 minutes prevents creating a DDoS of retries against YouTube.

---

## Error Detection Logic

In `get_transcript_with_diagnostics()`:

```python
error_str = str(e).lower()

if "403" in error_str or "forbidden" in error_str:
    error = YouTubeBlockedError("HTTP 403 Forbidden")
elif "429" in error_str or "too many requests" in error_str:
    error = YouTubeBlockedError("HTTP 429 Too Many Requests")
elif "captcha" in error_str:
    error = YouTubeBlockedError("CAPTCHA required")
elif "not available" in error_str or "no captions" in error_str:
    error = NoTranscriptError()
else:
    error = YouTubeBlockedError(f"Error: {str(e)[:50]}")
```

**Why string matching?** Different Python libraries throw different exception types. String matching in error message is more reliable across library versions.

---

## Testing

### Run tests:
```bash
pytest Backend/tests/test_transcript_reliability.py -v
```

### Test scenarios covered:

1. **403 Forbidden**
   ```python
   mock_get_transcript.side_effect = Exception("HTTP 403: Forbidden")
   # Assert: 502 status, error_code="youtube_blocked"
   ```

2. **429 Rate Limited**
   ```python
   mock_get_transcript.side_effect = Exception("HTTP 429: Too Many Requests")
   # Assert: 502 status, error_code="youtube_blocked"
   ```

3. **No Captions**
   ```python
   mock_get_transcript.return_value = None
   # Assert: 502 status, error_code="no_transcript"
   ```

4. **Generation Service Down**
   ```python
   mock_generate_blog.side_effect = Exception("Service unavailable")
   # Assert: 503 status, error_code="generation_failed"
   ```

5. **Successful Generation**
   ```python
   # All mocks return valid data
   # Assert: 200 status, content in response
   ```

6. **Cache prevents retries**
   ```python
   # Call twice with same video_id
   # Assert: get_transcript called only once (second use cache)
   ```

---

## Deployment Checklist

- [ ] Install `pytest-django` in `requirements.txt`
- [ ] Run tests: `pytest Backend/tests/test_transcript_reliability.py`
- [ ] Push changes to main branch
- [ ] Deploy to EC2
- [ ] SSH to EC2 and run diagnostics:
  ```bash
  cd /app/Backend
  python manage.py diagnose_transcript "https://youtube.com/watch?v=..."
  ```
- [ ] Monitor logs for error_codes: `grep "error_code\|youtube_blocked" /var/log/app.log`
- [ ] If 403 errors appear:
  - [ ] Check EC2 security group IP allowlisting
  - [ ] Consider using residential proxy
  - [ ] Document workaround (suggest MP3 upload to users)

---

## Fallback Strategy (For Users)

1. **YouTube blocked:** Suggest MP3 upload button (already in UI)
2. **No captions:** "This video has no captions. Upload MP3 instead."
3. **Service down:** "Note generation temporarily unavailable. Try again in 5 minutes."

All these messages are **surfaced in the error message** that the frontend displays.

---

## Monitoring Queries

### In CloudWatch / ELK:
```
error_code=youtube_blocked      # YouTube blocking issues
error_code=no_transcript        # Video has no captions
error_code=generation_failed    # OpenAI service issues
status=502                      # Upstream errors (sum of above)
status=503                      # Service degradation
```

This lets ops see **what's failing and why**, not just "error rate up 5%".
