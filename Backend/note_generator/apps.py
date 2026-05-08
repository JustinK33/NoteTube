from django.apps import AppConfig


class NoteGeneratorConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "note_generator"

    def ready(self):
        # Importing registers the @receiver handlers in rag/signals.py.
        from note_generator.rag import signals  # noqa: F401
