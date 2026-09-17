from .settings import *

SECRET_KEY = "test-secret-key-for-testing-only-not-for-production"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }
}

# ADD THIS - Override Redis cache with local memory cache
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "test-cache",
    }
}


class DisableMigrations:
    def __contains__(self, item):
        return True

    def __getitem__(self, item):
        return None


MIGRATION_MODULES = DisableMigrations()

STATICFILES_DIRS = []
STATIC_ROOT = None

# No PGVector reachable here, and eager Celery would run embedding for real on
# every NotePost save, fail, and retry three times behind a swallowed exception.
RAG_EMBED_ON_SAVE = False

# settings.py calls load_dotenv(), so a developer's real REDIS_URL leaks into the
# test run and RedisSemanticCache would open a live connection plus an OpenAI
# embeddings call. Empty makes _configure_semantic_cache_once a no-op.
REDIS_URL = ""

# Run tasks synchronously and in-process; no Redis, no worker needed
CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = False
CELERY_TASK_STORE_EAGER_RESULT = True
CELERY_BROKER_URL = "memory://"
CELERY_RESULT_BACKEND = "cache+memory://"
