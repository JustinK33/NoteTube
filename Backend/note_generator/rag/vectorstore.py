"""Singletons for the RAG layer.

Both `OpenAIEmbeddings` and `PGVector` open network connections / pools on
construction, so we cache them per-process via `lru_cache`. Tests can call
`.cache_clear()` on either factory to force a rebuild.
"""

from functools import lru_cache

from django.conf import settings
from langchain_openai import OpenAIEmbeddings
from langchain_postgres import PGVector


@lru_cache(maxsize=1)
def get_embeddings() -> OpenAIEmbeddings:
    return OpenAIEmbeddings(
        model=settings.RAG_EMBEDDING_MODEL,
        api_key=settings.OPENAI_API_KEY,
    )


@lru_cache(maxsize=1)
def get_vectorstore() -> PGVector:
    return PGVector(
        embeddings=get_embeddings(),
        collection_name=settings.RAG_COLLECTION_NAME,
        connection=settings.PGVECTOR_CONNECTION_STRING,
        use_jsonb=True,
    )
