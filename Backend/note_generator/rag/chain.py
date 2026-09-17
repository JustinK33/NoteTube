"""Self-correcting retrieval, per-user, with RedisSemanticCache for the LLM step.

Retrieval is graded before it is used. If the notes that came back can't answer
the question, the grader hands back a rewritten query, the search runs again,
and it grades again, up to `settings.RAG_MAX_RETRIES` extra passes. If nothing
works the answer is still generated, but from a prompt that says so, and the
caller gets `low_confidence=True` to render it differently.

The loop is plain Python rather than LCEL. A bounded retry has no natural
runnable primitive without pulling in langgraph, and `for`/`break` already say
it. The two leaves stay LCEL (`prompt | llm | parser`), which is also the part
of the LangChain API least likely to move under us.

Note on granularity: there is one vector per whole note (see rag/embed.py), not
per transcript chunk. So a rewrite can re-rank notes but cannot surface a better
passage inside a note that was already retrieved. That caps what this loop can
recover; chunking is the fix if the stats show retries rarely helping.
"""

import logging
import time

from django.conf import settings
from langchain_core.globals import set_llm_cache
from langchain_core.documents import Document
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI
from langchain_redis import RedisSemanticCache

from note_generator.models import (
    GRADE_INSUFFICIENT,
    GRADE_UNPARSEABLE,
    OUTCOME_AFTER_RETRY,
    OUTCOME_EXHAUSTED,
    OUTCOME_FIRST_PASS,
)
from note_generator.rag.grade import GRADER_PROMPT, parse_grade
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


_SYSTEM = (
    "You are NoteTube's research assistant. Answer the user's question using "
    "ONLY the excerpts from their saved notes provided below. If the notes "
    "don't contain the answer, say so plainly - do not invent facts. When you "
    "draw on a specific note, cite it inline like [note 42]."
)

_PROMPT = ChatPromptTemplate.from_messages(
    [
        ("system", _SYSTEM),
        ("user", "Notes:\n\n{context}\n\nQuestion: {question}"),
    ]
)

# Used when every retrieval pass was graded insufficient. Answering anyway beats
# a dead end, because these are the user's own notes and the source chips let
# them judge, but the hedge has to be in the answer and not just in the UI.
_LOW_CONFIDENCE_PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            _SYSTEM + "\n\n"
            "These excerpts were judged not to cover the question well. Open "
            "with one short sentence saying the saved notes may not cover this "
            "topic, then give whatever partial answer the excerpts genuinely "
            "support. Do not pad it out and do not guess past what is written.",
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


def _derive_outcome(grades: list[str]) -> str:
    """Only the final grade decides, because the loop stops as soon as a pass
    is not INSUFFICIENT."""
    if grades[-1] == GRADE_INSUFFICIENT:
        return OUTCOME_EXHAUSTED
    return OUTCOME_FIRST_PASS if len(grades) == 1 else OUTCOME_AFTER_RETRY


def answer_question(user_id: int, question: str) -> dict:
    """Retrieve, grade, retry on a rewritten query, then answer.

    Touches no database, so the telemetry it returns can be persisted by the
    caller and the whole loop stays testable without one.

    Returns keys: answer, docs, low_confidence, outcome, attempts, grades,
    rewritten_queries, first_doc_count, final_doc_count, latency_ms.
    """
    started = time.monotonic()
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
    grader = GRADER_PROMPT | llm | StrOutputParser()

    max_passes = 1 + max(settings.RAG_MAX_RETRIES, 0)
    query = question
    docs: list[Document] = []
    first_docs: list[Document] | None = None
    grades: list[str] = []
    rewrites: list[str] = []

    for pass_index in range(max_passes):
        docs = retriever.invoke(query)
        if first_docs is None:
            first_docs = docs

        try:
            grade, rewrite = parse_grade(
                grader.invoke({"question": question, "context": _format_docs(docs)})
            )
        except Exception as e:
            # A grader outage degrades to the old single-pass behaviour rather
            # than failing the search outright.
            logger.warning(
                f"grader failed on pass {pass_index + 1}, using docs as-is: {e}"
            )
            grade, rewrite = GRADE_UNPARSEABLE, ""

        grades.append(grade)
        if grade != GRADE_INSUFFICIENT:
            break
        if pass_index == max_passes - 1:
            break
        if not rewrite:
            # Nothing to search for next. Retrying the same query would just
            # spend another grader call on the same documents.
            logger.warning("grader said INSUFFICIENT but gave no rewrite, stopping")
            break

        rewrites.append(rewrite)
        query = rewrite

    # The grader always sees the original question, never a rewrite, so it is
    # judging what the user actually asked at every pass.
    outcome = _derive_outcome(grades)
    low_confidence = outcome == OUTCOME_EXHAUSTED

    prompt = _LOW_CONFIDENCE_PROMPT if low_confidence else _PROMPT
    answer = (prompt | llm | StrOutputParser()).invoke(
        {"question": question, "context": _format_docs(docs)}
    )

    return {
        "answer": answer,
        "docs": docs,
        "low_confidence": low_confidence,
        "outcome": outcome,
        "attempts": len(grades),
        "grades": grades,
        "rewritten_queries": rewrites,
        "first_doc_count": len(first_docs or []),
        "final_doc_count": len(docs),
        "latency_ms": int((time.monotonic() - started) * 1000),
    }
