"""Retrieval grader: does what came back actually answer the question?

The grader returns a verdict and, when the excerpts fall short, a rewritten
query aimed at whatever is missing. Output is two lines of plain text rather
than a structured-output call, which keeps `parse_grade` unit-testable with no
LLM and no dependency on LangChain's structured-output machinery.

Anything that doesn't match the contract is UNPARSEABLE, and the caller treats
that as sufficient. Failing open matters twice: a formatting glitch shouldn't
burn two extra LLM calls, and it shouldn't land in the stats as a genuine
INSUFFICIENT, which would inflate the baseline failure rate the whole
measurement rests on.
"""

import re

from langchain_core.prompts import ChatPromptTemplate

from note_generator.models import (
    GRADE_INSUFFICIENT,
    GRADE_SUFFICIENT,
    GRADE_UNPARSEABLE,
)

# ponytail: two-line text contract, switch to with_structured_output if the
# model starts drifting off format often enough to show up in the stats as a
# rising UNPARSEABLE count.
GRADER_PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            "You grade retrieval quality for NoteTube, which searches notes "
            "generated from the transcripts of YouTube videos and audio "
            "recordings.\n\n"
            "Decide whether the excerpts below contain enough information to "
            "answer the question. Judge coverage only. Do not answer the "
            "question yourself.\n\n"
            "Reply in exactly two lines, nothing else:\n"
            "VERDICT: SUFFICIENT or INSUFFICIENT\n"
            "REWRITE: a search query\n\n"
            "If the verdict is SUFFICIENT, repeat the original question on the "
            "REWRITE line.\n"
            "If it is INSUFFICIENT, write a query targeting what is missing. "
            "The indexed text is people talking on video, so prefer the "
            "phrasing a speaker would actually use over formal terminology, try "
            "synonyms, and widen the scope if the question is too specific to "
            "match or narrow it if it is too vague. Output the query alone, "
            "with no explanation.",
        ),
        ("user", "Excerpts:\n\n{context}\n\nQuestion: {question}"),
    ]
)

_VERDICT_LINE_RE = re.compile(r"^\s*VERDICT\s*:\s*(.+)$", re.IGNORECASE | re.MULTILINE)
_REWRITE_LINE_RE = re.compile(r"^\s*REWRITE\s*:\s*(.+)$", re.IGNORECASE | re.MULTILINE)

# Markdown bold, quotes and trailing punctuation the model sometimes wraps the
# verdict in. Stripped before the exact comparison below.
_DECORATION = "*_`\"' .:"


def _extract_verdict(text: str) -> str | None:
    """Pull the verdict from a labelled line, else from the first line.

    Deliberately an exact match after stripping decoration rather than a
    substring scan: "not sufficient" anywhere in a chatty reply would otherwise
    read as SUFFICIENT, which is the one direction we cannot afford to guess.
    """
    match = _VERDICT_LINE_RE.search(text)
    if match:
        candidate = match.group(1)
    else:
        lines = [line for line in text.splitlines() if line.strip()]
        if not lines:
            return None
        candidate = lines[0]

    token = candidate.strip().strip(_DECORATION).upper()
    if token == GRADE_INSUFFICIENT:
        return GRADE_INSUFFICIENT
    if token == GRADE_SUFFICIENT:
        return GRADE_SUFFICIENT
    return None


def parse_grade(text: str) -> tuple[str, str]:
    """Return (grade, rewritten_query).

    The rewrite is empty unless the grade is INSUFFICIENT, so callers can trust
    a non-empty rewrite to mean "there is somewhere else worth looking" and the
    stored rewrite history stays free of echoed-back questions.
    """
    if not text or not text.strip():
        return GRADE_UNPARSEABLE, ""

    verdict = _extract_verdict(text)
    if verdict is None:
        return GRADE_UNPARSEABLE, ""
    if verdict == GRADE_SUFFICIENT:
        return GRADE_SUFFICIENT, ""

    rewrite = ""
    match = _REWRITE_LINE_RE.search(text)
    if match:
        rewrite = match.group(1).strip().strip(_DECORATION).strip()
    return GRADE_INSUFFICIENT, rewrite
