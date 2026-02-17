# YouTube Blocking Troubleshooting Guide

## What You're Seeing

When you paste a YouTube link, you're getting:

```
YouTube blocked access. Try MP3 upload or paste transcript. (Error: yt-dlp failed: ...)
```

This is **expected behavior** when YouTube blocks your server's IP address. The system is correctly:
1. ✅ Detecting the error
2. ✅ Returning a structured error response (not a 500 crash)
3. ✅ Showing a helpful message with fallback suggestions
4. ✅ Suggesting the MP3 upload alternative

## Why This Happens

YouTube blocks requests from certain IP ranges to prevent scraping. This is particularly common on:
- **Cloud servers** (AWS EC2, Google Cloud, Azure, etc.)
- **Shared hosting** (IPs used by many customers)
- **High-volume scraping** (repeated requests from same IP)

When YouTube's servers detect suspicious activity from your IP:
- **403 Forbidden** - Your IP is completely blocked
- **429 Too Many Requests** - Rate limited (too many requests in short time)
- **CAPTCHA challenges** - Required to verify human access

## How to Fix It

### Option 1: Use MP3 Upload Feature ✅ (Recommended)
The frontend now supports uploading MP3 files directly:
1. Click the "Upload MP3" tab on the website
2. Select your MP3 file
3. Click "Generate Notes"

**No YouTube API calls needed** - fully bypasses the blocking issue.

### Option 2: Paste Transcript Manually (When Available)
If the video has captions but `yt-dlp` can't access them:
1. Open the YouTube video
2. Click the "CC" (captions) button
3. Copy the transcript text
4. Manually paste into notes

### Option 3: Use a Proxy/VPN (For Development)
To test YouTube features locally:
```bash
# Using a proxy service (example)
export http_proxy=http://proxy.example.com:8080
export https_proxy=http://proxy.example.com:8080

# Restart your container with proxy env vars
docker compose down
docker compose up
```

### Option 4: Request IP Whitelist (For Production AWS)
Contact YouTube/Google if you're running a legitimate educational service. You may be able to request whitelisting for your IP range.

### Option 5: Use YouTube API Key (Alternative Approach)
Instead of `yt-dlp`, use the official YouTube Captions API:

```python
# Alternative (not currently implemented)
# Requires YouTube API key and OAuth for captions retrieval
from youtube_transcript_api import YouTubeTranscriptApi
```

## Diagnostic Commands

### Check Which Error You're Getting

```bash
# SSH into your server/container
cd /Users/justin/Projects/NoteTube

# Run the diagnostic command
python Backend/manage.py diagnose_transcript "https://www.youtube.com/watch?v=VIDEO_ID"
```

Example output:
```
📋 Diagnosing transcript fetch for:
   https://www.youtube.com/watch?v=dQw4w9WgXcQ

✓ Video ID extracted: dQw4w9WgXcQ
✗ Fetch failed: RuntimeError

💡 HTTP Error from yt-dlp:
   YouTube is blocking this IP address
   
   Solutions:
   - Use the MP3 upload feature instead
   - Try from a different network/IP
   - Use the YouTube API instead of yt-dlp
```

### Check Docker Logs

```bash
# View Django error logs
docker compose logs -f backend

# Look for lines containing "yt-dlp" or "transcript" to see the actual error
```

### Check if yt-dlp Works in Container

```bash
# SSH into the container
docker exec -it notetube-backend bash

# Test yt-dlp directly
yt-dlp -f bestaudio -o /tmp/test.m4a "https://www.youtube.com/watch?v=dQw4w9WgXcQ"

# If blocked, you'll see:
# ERROR: [youtube] dQw4w9WgXcQ: Unable to extract video data
# ERROR: [youtube] dQw4w9WgXcQ: YouTube said: ...
```

## Current Error Handling (How It Works)

The system is now robust with proper error handling:

```
YouTube API Call
    ↓
yt-dlp Tries to Download Audio
    ↓
Error Occurs (403/429/Connection Timeout)
    ↓
Exception Caught in transcript_utils.py
    ↓
Error Type Detected (YouTube Blocked / Rate Limited / etc)
    ↓
Converted to Structured Error Response
    ↓
Frontend Displays: "YouTube blocked access. Try MP3 upload..."
    ↓
User Can Fallback to MP3 Upload ✅
```

## Cache Behavior

Errors are cached to prevent repeated failed requests:
- **YouTube blocked errors**: Cached for **10 minutes** to avoid DDoS-like retry behavior
- **Success transcripts**: Cached for **1 hour** to improve performance

To clear the cache (useful for testing):

```bash
# Using Django shell
docker exec -it notetube-backend python Backend/manage.py shell

# In the shell:
from django.core.cache import cache
cache.clear()
exit()
```

## Monitoring

### Check for Blocked Videos in Logs

```bash
# View recent errors in container logs
docker compose logs backend | grep -i "youtube_blocked\|403\|429\|captcha"
```

### Check Cache Hit Rate

The error messages in logs will tell you:
- `"Returning cached transcript"` = Cache hit (good performance)
- `"Fetching transcript"` = Cache miss (actual API call)
- `"Returning cached error"` = Cached error (prevents retry storm)

## Testing

To verify the MP3 fallback works:

```bash
# Create a test MP3 file
# Or download a sample: https://file-examples.com/storage/fe5166a7a2a3d0e3a0d43e8/2017-10-21-Haydn-Surprise-01-1.mp3

# Upload via the web UI → "Upload MP3" tab
# Should generate notes without any YouTube API calls
```

## Architecture Impact

### Before (Fragile)
```
YouTube Link → yt-dlp → 403 Error → Generic "Something went wrong" → User confused
```

### After (Robust)
```
YouTube Link → yt-dlp → 403 Error → Structured error handling
    → Returns error_code: "youtube_blocked"
    → Frontend shows helpful message with MP3 fallback
    → User knows what to do next ✅
```

## Summary

**Your implementation is working correctly!**

The error message you're seeing means:
- ✅ YouTube blocking is detected
- ✅ Error is handled gracefully
- ✅ User gets helpful guidance
- ✅ MP3 upload fallback is available

Simply use the MP3 upload feature when YouTube blocks access. This is the intended behavior for production environments where direct YouTube access may be restricted.

---

**Questions?** Check the logs with:
```bash
docker compose logs -f backend | grep -i "transcript\|error"
```
