"""Wire NotePost lifecycle into the embedding index.

post_save on NotePost  -> embed (or re-embed if content hash changed)
post_delete on NoteEmbedding -> drop the orphaned PGVector row. We hook the
bookkeeping table rather than NotePost itself so that cascade deletes, manual
embedding deletes, and direct NotePost deletes all funnel through the same
cleanup path.

LangChain is imported lazily inside the handlers so that `manage.py` commands
(migrations, collectstatic, etc.) don't pay the import cost or fail in
environments that haven't installed the RAG deps yet.
"""

import logging

from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from note_generator.models import NoteEmbedding, NotePost

logger = logging.getLogger(__name__)


@receiver(post_save, sender=NotePost)
def _embed_on_note_save(sender, instance: NotePost, **kwargs):
    # Enqueue async — never block the HTTP response for an OpenAI embeddings call.
    try:
        from note_generator.tasks import embed_note_task

        embed_note_task.delay(instance.id)
    except Exception as e:
        logger.exception(
            f"Failed to enqueue embed_note_task for note {instance.id}: {e}"
        )


@receiver(post_delete, sender=NoteEmbedding)
def _drop_vector_on_embedding_delete(sender, instance: NoteEmbedding, **kwargs):
    try:
        from note_generator.rag.embed import delete_embedding

        delete_embedding(instance.vector_id)
    except Exception as e:
        logger.warning(f"delete_embedding failed for vector {instance.vector_id}: {e}")
