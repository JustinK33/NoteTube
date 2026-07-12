try:  # ponytail: celery absent in slim Vercel deploy; skip if unavailable
    from .celery import app as celery_app

    __all__ = ("celery_app",)
except ModuleNotFoundError:
    __all__ = ()
