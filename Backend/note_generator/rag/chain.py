"""LCEL retrieval chain, per-user, with RedisSemanticCache for the LLM step.

The retriever is built per request because its metadata filter is keyed on
user_id; everything else (prompt, LLM, parser, cache) is shared.

Pipeline shape:
    {"question": str}
        -> assign docs   (retriever.invoke(question))
        -> assign context (format docs into a string)
        -> assign answer (prompt | llm | parser)
"""

import logging
from typing import Any

from django.conf import settings
from langchain_core.globals import set_llm_cache
from langchain_core.documents import Document
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnablePassthrough
from langchain_openai import ChatOpenAI
from langchain_redis import RedisSemanticCache

from note_generator.rag.vectorstore import get_embeddings, get_vectorstore

logger = logging.getLogger(__name__)

_cache_configured = False


def _configure_semantic_cache_once() -> None:
    """Install RedisSemanticCache as the global LangChain LLM cache.

    Called lazily on first chain construction so Django startup / migrations
    don't try to talk to Redis. Re-running is a no-op.
    """
    global _cache_configured
    if _cache_configured or not settings.REDIS_URL:
        return
    try:
        set_llm_cache(
            RedisSemanticCache(
                redis_url=settings.REDIS_URL,
                embeddings=get_embeddings(),
                distance_threshold=settings.RAG_SEMANTIC_CACHE_THRESHOLD,
            )
        )
        _cache_configured = True
    except Exception as e:
        logger.warning(f"RedisSemanticCache init failed, continuing uncached: {e}")


_PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            "You are NoteTube's research assistant. Answer the user's question using "
            "ONLY the excerpts from their saved notes provided below. If the notes "
            "don't contain the answer, say so plainly — do not invent facts. When you "
            "draw on a specific note, cite it inline like [note 42].",
        ),
        ("user", "Notes:\n\n{context}\n\nQuestion: {question}"),
    ]
)


def _format_docs(docs: list[Document]) -> str:
    if not docs:
        return "(no relevant notes found)"
    return "\n\n---\n\n".join(
        f"[note {d.metadata.get('note_id')}] {d.metadata.get('title', '')}\n"
        f"{d.page_content}"
        for d in docs
    )


def build_user_chain(user_id: int):
    """Return an LCEL chain scoped to a single user's notes.

    Input:  {"question": str}
    Output: {"question": str, "docs": list[Document], "context": str, "answer": str}
    """
    _configure_semantic_cache_once()

    retriever = get_vectorstore().as_retriever(
        search_kwargs={
            "k": settings.RAG_TOP_K,
            "filter": {"user_id": user_id},
        }
    )

    llm = ChatOpenAI(
        model=settings.RAG_CHAT_MODEL,
        api_key=settings.OPENAI_API_KEY,
        temperature=0,
    )

    generate = _PROMPT | llm | StrOutputParser()

    return (
        RunnablePassthrough.assign(docs=lambda x: retriever.invoke(x["question"]))
        | RunnablePassthrough.assign(context=lambda x: _format_docs(x["docs"]))
        | RunnablePassthrough.assign(answer=generate)
    )
