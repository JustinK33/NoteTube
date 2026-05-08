"""Idempotent embedding writer for NotePosts.

`embed_note` is the entrypoint: it sha256s the note body, compares against the
last hash we wrote (NoteEmbedding.content_hash), and either no-ops, replaces,
or inserts the vector in PGVector. Every vector carries the owning user_id in
its metadata so retrieval can filter on it.
"""

import hashlib
import logging
import uuid

from langchain_core.documents import Document

from note_generator.models import NoteEmbedding, NotePost
from note_generator.rag.vectorstore import get_vectorstore

logger = logging.getLogger(__name__)


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def embed_note(note: NotePost) -> None:
    """Upsert this note's embedding in PGVector. No-op if content unchanged."""
    new_hash = _content_hash(note.generated_content or "")
    existing = NoteEmbedding.objects.filter(note=note).first()
    if existing and existing.content_hash == new_hash:
        return

    vs = get_vectorstore()

    # Drop the stale vector before writing the replacement so we don't end up
    # with two embeddings for the same note in the index.
    if existing:
        try:
            vs.delete(ids=[existing.vector_id])
        except Exception as e:
            logger.warning(f"PGVector delete failed for note {note.id}: {e}")

    vector_id = str(uuid.uuid4())
    doc = Document(
        page_content=note.generated_content,
        metadata={
            "note_id": note.id,
            "user_id": note.user_id,
            "title": note.youtube_title,
            "source": note.youtube_link or "",
        },
    )
    vs.add_documents([doc], ids=[vector_id])

    NoteEmbedding.objects.update_or_create(
        note=note,
        defaults={"vector_id": vector_id, "content_hash": new_hash},
    )


def delete_embedding(vector_id: str) -> None:
    """Remove an orphaned vector from PGVector (called from post_delete)."""
    try:
        get_vectorstore().delete(ids=[vector_id])
    except Exception as e:
        logger.warning(f"PGVector delete failed for vector {vector_id}: {e}")
