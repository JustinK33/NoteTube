# Quick Reference: Using the Reliability Features

## For Developers

### Running Tests Locally:
```bash
cd Backend
pytest tests/test_transcript_reliability.py -v
```

### Checking specific error scenario:
```bash
# 403 Forbidden test
pytest tests/test_transcript_reliability.py::TestGenerateNoteEndpoint::test_transcript_fetch_403_blocked -v

# 429 Rate Limited test
pytest tests/test_transcript_reliability.py::TestGenerateNoteEndpoint::test_transcript_fetch_429_rate_limited -v

# Caching test
pytest tests/test_transcript_reliability.py::TestTranscriptCaching -v
```

---

## For DevOps / EC2 Operators

### Diagnose a failing video (SSH to EC2):
```bash
cd /app
python Backend/manage.py diagnose_transcript "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
```

**Output example:**
```
✓ Video ID extracted: dQw4w9WgXcQ
✗ Fetch failed: HTTPError
  Details: HTTP 403: Forbidden

💡 HTTP 403 (Forbidden): YouTube is blocking this IP.
   Solution: Use a proxy/VPN or check AWS IP restrictions.
```

### Check logs for errors:
```bash
# All YouTube blocks
grep "youtube_blocked" /var/log/app.log | tail -20

# All missing transcripts
grep "no_transcript" /var/log/app.log | tail -20

# High-level summary
grep "error_code" /var/log/app.log | cut -d= -f2 | sort | uniq -c | sort -rn
```

### Clear transcript cache (if needed):
```bash
cd /app
python Backend/manage.py shell
>>> from django.core.cache import cache
>>> cache.delete_pattern("transcript:video:*")
>>> # or
>>> cache.clear()
```

---

## For Frontend Integration

### Expected API Response Format:

**Success:**
```json
{
  "content": "# Generated Notes\n\n..."
}
```

**Error:**
```json
{
  "error_code": "youtube_blocked",  // or "no_transcript", "generation_failed", etc.
  "message": "YouTube blocked access. Try MP3 upload or paste transcript. (HTTP 403 Forbidden)"
}
```

HTTP status codes:
- `400` - Invalid input (malformed URL)
- `500` - Internal server error (save failed)
- `502` - Bad Gateway (YouTube issue - 403/429/blocked)
- `503` - Service Unavailable (generation service down)

### Handling errors in frontend:
```javascript
if (response.ok) {
    const data = await response.json();
    displayContent(data.content);
} else {
    const data = await response.json();
    displayError(data.error_code, data.message);
}
```

---

## Caching Behavior

### What gets cached:
- ✅ Successful transcripts: **1 hour**
- ✅ 403/429/Captcha errors: **10 minutes**
- ✅ No-caption errors: **1 hour** (won't change)

### Cache key format:
```
transcript:video:{11_char_video_id}
```

### Example:
```
transcript:video:dQw4w9WgXcQ  → "SRT content..." or {"is_error": true, "error": {...}}
```

---

## Troubleshooting

### User says "Something went wrong" appears for every video:

**Step 1:** Run diagnostics on a problematic video URL
```bash
python Backend/manage.py diagnose_transcript "https://youtube.com/watch?v=..."
```

**Step 2:** Based on output, check:
- **403 Forbidden?** → EC2 IP blocked by YouTube
- **429 Too Many?** → Rate limited; wait or cache cleared too early
- **CAPTCHA?** → YouTube thinks bot; consider proxy/residential IP
- **Connection error?** → EC2 internet connectivity issue

**Step 3:** Check logs:
```bash
tail -f /var/log/app.log | grep "Transcript fetch failed"
```

### Transcript cache is stale (user expecting new transcript):

Clear cache for specific video:
```bash
python Backend/manage.py shell
>>> from django.core.cache import cache
>>> cache.delete("transcript:video:dQw4w9WgXcQ")
```

Or clear all:
```bash
>>> cache.clear()
```

### Test says "mock_get_transcript.side_effect" but shouldn't:

Make sure you're importing the function to be mocked from the **view module**:
```python
# Correct
@patch("note_generator.views.get_transcript")

# Wrong (won't mock in view)
@patch("note_generator.get_transcript")
```

---

## Monitoring / Alerting

### Set up alerts on CloudWatch for:

**Rate-limited (429):**
```
error_code == "youtube_blocked" AND "429" in message
```
→ Action: Message ops, consider MP3-only mode temporarily

**All videos failing:**
```
error_code in ["youtube_blocked", "no_transcript", "generation_failed"]
  AND count > 10 in last 5 min
```
→ Action: Page on-call engineer

**High error rate (>5%):**
```
http_status in [502, 503] AND count > 50 in last 5 min
```
→ Action: Check YouTube status / service health

---

## FAQ

**Q: Why 502 instead of 500 for YouTube blocks?**
A: 502 (Bad Gateway) tells load balancers/proxies that it's an upstream issue, not a server bug. They won't immediately retry or mark as unhealthy.

**Q: Why cache failures for 10 minutes?**
A: Rate limit errors are temporary. 10 minutes gives YouTube time to cool down. Caching prevents creating a DDoS of retries.

**Q: How does caching help if user refreshes page?**
A: If 100 users try the same video in 1 hour, only the first hits YouTube; the other 99 use cache. Reduces load + speeds up page.

**Q: What if cache backend is Redis and Redis is down?**
A: Django cache gracefully falls back to no-cache. Transcript fetches still work, just slower/more YouTube requests.

**Q: Can user bypass MP3 and paste transcript directly?**
A: Not yet, but that's the fallback UX mentioned in error messages. Consider adding a "paste transcript" field if YouTube issues persist.
