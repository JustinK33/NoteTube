# ponytail: standalone placeholder so Vercel skips Django collectstatic + heavy deps.
# Swap back to `from notetube.wsgi import application as app` once on a host that fits the full app.
def app(environ, start_response):
    start_response("200 OK", [("Content-Type", "text/html; charset=utf-8")])
    return [b"<h1>NoteTube</h1><p>Deployed. App not wired up on this host yet.</p>"]
