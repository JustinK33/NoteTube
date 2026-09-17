"""Tests for the self-correcting retrieval loop.

The whole point of the loop is a bounded number of LLM calls, so most of these
assert exact call counts rather than just the final answer. A loop that quietly
runs four times still returns a plausible answer, which is what makes it
expensive and hard to notice.

Seams, all patched in the consuming module's namespace:
  note_generator.rag.chain.get_vectorstore  -> scripted document batches
  note_generator.rag.chain.ChatOpenAI       -> FakeListChatModel, no network
  note_generator.rag.chain.parse_grade      -> only for the grader-raises case
  note_generator.rag.chain.answer_question  -> only for the endpoint case
"""

import json
from io import StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import Client, TestCase
from django.urls import reverse
from langchain_core.documents import Document
from langchain_core.language_models.fake_chat_models import FakeListChatModel

from note_generator.models import (
    GRADE_INSUFFICIENT,
    GRADE_SUFFICIENT,
    GRADE_UNPARSEABLE,
    OUTCOME_AFTER_RETRY,
    OUTCOME_EXHAUSTED,
    OUTCOME_FIRST_PASS,
    NotePost,
    RetrievalAttempt,
)
from note_generator.rag.chain import answer_question
from note_generator.rag.grade import parse_grade


def _doc(note_id: int, title: str = "A note", body: str = "some content"):
    return Document(
        page_content=body,
        metadata={"note_id": note_id, "title": title, "source": ""},
    )


class FakeRetriever:
    """Records every query it is asked for, so the rewrite can be asserted."""

    def __init__(self, batches):
        self.batches = batches
        self.queries = []

    def invoke(self, query, **kwargs):
        self.queries.append(query)
        # Past the scripted batches, keep returning the last one.
        index = min(len(self.queries) - 1, len(self.batches) - 1)
        return self.batches[index]


class FakeVectorStore:
    def __init__(self, retriever):
        self._retriever = retriever

    def as_retriever(self, **kwargs):
        return self._retriever


def _grade_text(verdict: str, rewrite: str = "") -> str:
    return f"VERDICT: {verdict}\nREWRITE: {rewrite}"


class ParseGradeTest(TestCase):
    """No mocks, no LLM. The parser is the part most likely to rot silently."""

    def test_sufficient_has_no_rewrite(self):
        assert parse_grade(_grade_text(GRADE_SUFFICIENT, "echoed question")) == (
            GRADE_SUFFICIENT,
            "",
        )

    def test_insufficient_returns_rewrite(self):
        grade, rewrite = parse_grade(
            _grade_text(GRADE_INSUFFICIENT, "react hooks talk")
        )
        assert grade == GRADE_INSUFFICIENT
        assert rewrite == "react hooks talk"

    def test_tolerates_case_whitespace_and_markdown(self):
        text = (
            "  verdict :  **insufficient**  \n\n  Rewrite:   *what he said about "
            "state*  "
        )
        assert parse_grade(text) == (GRADE_INSUFFICIENT, "what he said about state")

    def test_bare_verdict_without_label(self):
        assert parse_grade("SUFFICIENT") == (GRADE_SUFFICIENT, "")

    def test_negation_is_not_read_as_sufficient(self):
        # A substring scan for "SUFFICIENT" would call this a pass.
        grade, _ = parse_grade("VERDICT: not sufficient\nREWRITE: something else")
        assert grade == GRADE_UNPARSEABLE

    def test_garbage_and_empty_are_unparseable(self):
        assert parse_grade("I think maybe the notes are okay?") == (
            GRADE_UNPARSEABLE,
            "",
        )
        assert parse_grade("") == (GRADE_UNPARSEABLE, "")
        assert parse_grade("   \n  ") == (GRADE_UNPARSEABLE, "")

    def test_insufficient_with_missing_rewrite_line(self):
        assert parse_grade("VERDICT: INSUFFICIENT") == (GRADE_INSUFFICIENT, "")


class AnswerQuestionTest(TestCase):
    """The loop itself. answer_question touches no DB, so no fixtures needed."""

    def _run(self, grader_replies, batches=None, answer="the answer"):
        """Drive one answer_question call with scripted model output.

        The grader and the generator share one llm instance, so FakeListChatModel
        serves the grader replies in order and then the answer.
        """
        retriever = FakeRetriever(batches or [[_doc(1)]])
        fake_llm = FakeListChatModel(responses=[*grader_replies, answer])
        with patch(
            "note_generator.rag.chain.get_vectorstore",
            return_value=FakeVectorStore(retriever),
        ), patch("note_generator.rag.chain.ChatOpenAI", return_value=fake_llm):
            result = answer_question(user_id=1, question="what did he say about hooks?")
        return result, retriever

    def test_sufficient_first_pass_does_not_retry(self):
        result, retriever = self._run([_grade_text(GRADE_SUFFICIENT)])

        assert retriever.queries == ["what did he say about hooks?"]
        assert result["attempts"] == 1
        assert result["grades"] == [GRADE_SUFFICIENT]
        assert result["rewritten_queries"] == []
        assert result["outcome"] == OUTCOME_FIRST_PASS
        assert result["low_confidence"] is False
        assert result["answer"] == "the answer"

    def test_retry_searches_with_the_rewritten_query(self):
        result, retriever = self._run(
            [
                _grade_text(GRADE_INSUFFICIENT, "hooks useState useEffect"),
                _grade_text(GRADE_SUFFICIENT),
            ],
            batches=[[_doc(1)], [_doc(2), _doc(3)]],
        )

        # The rewrite, not the original question, drives the second search.
        assert retriever.queries == [
            "what did he say about hooks?",
            "hooks useState useEffect",
        ]
        assert result["attempts"] == 2
        assert result["outcome"] == OUTCOME_AFTER_RETRY
        assert result["rewritten_queries"] == ["hooks useState useEffect"]
        assert result["first_doc_count"] == 1
        assert result["final_doc_count"] == 2
        assert result["low_confidence"] is False

    def test_always_insufficient_stops_at_three_retrievals(self):
        result, retriever = self._run(
            [
                _grade_text(GRADE_INSUFFICIENT, "second try"),
                _grade_text(GRADE_INSUFFICIENT, "third try"),
                _grade_text(GRADE_INSUFFICIENT, "fourth try"),
            ]
        )

        # This exact count is the cap. 4 here means a runaway loop.
        assert len(retriever.queries) == 3
        assert result["attempts"] == 3
        assert result["grades"] == [GRADE_INSUFFICIENT] * 3
        # The fourth rewrite is never used, so it is never recorded.
        assert result["rewritten_queries"] == ["second try", "third try"]
        assert result["outcome"] == OUTCOME_EXHAUSTED
        assert result["low_confidence"] is True
        assert result["answer"], "an exhausted loop still answers, caveated"

    def test_blank_rewrite_stops_instead_of_researching(self):
        result, retriever = self._run(
            [_grade_text(GRADE_INSUFFICIENT, ""), _grade_text(GRADE_SUFFICIENT)]
        )

        assert len(retriever.queries) == 1
        assert result["attempts"] == 1
        assert result["outcome"] == OUTCOME_EXHAUSTED
        assert result["low_confidence"] is True

    def test_grader_failure_degrades_to_single_pass(self):
        retriever = FakeRetriever([[_doc(1)]])
        fake_llm = FakeListChatModel(responses=["the answer"])
        with patch(
            "note_generator.rag.chain.get_vectorstore",
            return_value=FakeVectorStore(retriever),
        ), patch("note_generator.rag.chain.ChatOpenAI", return_value=fake_llm), patch(
            "note_generator.rag.chain.parse_grade",
            side_effect=RuntimeError("grader is down"),
        ):
            result = answer_question(user_id=1, question="anything")

        # A grader outage is the old behaviour, not an error.
        assert len(retriever.queries) == 1
        assert result["grades"] == [GRADE_UNPARSEABLE]
        assert result["outcome"] == OUTCOME_FIRST_PASS
        assert result["low_confidence"] is False
        assert result["answer"] == "the answer"

    def test_unparseable_grade_is_treated_as_sufficient(self):
        result, retriever = self._run(["I'm not sure, the notes seem alright"])

        assert len(retriever.queries) == 1
        assert result["grades"] == [GRADE_UNPARSEABLE]
        assert result["outcome"] == OUTCOME_FIRST_PASS
        assert result["low_confidence"] is False


class SearchEndpointTest(TestCase):
    """POST enqueues, GET collects. Eager Celery runs the task inline."""

    def setUp(self):
        self.client = Client()
        User = get_user_model()
        self.user = User.objects.create_user(username="asker", password="pw-12345")
        self.client.login(username="asker", password="pw-12345")

    def _post(self, query="what about hooks?"):
        return self.client.post(
            reverse("api-notes-search"),
            json.dumps({"query": query}),
            content_type="application/json",
        )

    def test_post_returns_202_then_status_returns_the_answer(self):
        fake_result = {
            "answer": "He said to use useEffect. [note 1]",
            "docs": [_doc(1, title="React Hooks Deep Dive")],
            "low_confidence": True,
            "outcome": OUTCOME_EXHAUSTED,
            "attempts": 3,
            "grades": [GRADE_INSUFFICIENT] * 3,
            "rewritten_queries": ["a", "b"],
            "first_doc_count": 1,
            "final_doc_count": 1,
            "latency_ms": 1234,
        }
        with patch(
            "note_generator.rag.chain.answer_question", return_value=fake_result
        ):
            response = self._post()
            assert response.status_code == 202
            task_id = response.json()["task_id"]

            status = self.client.get(reverse("api-notes-search-status", args=[task_id]))

        assert status.status_code == 200
        body = status.json()
        assert body["status"] == "done"
        assert body["answer"] == "He said to use useEffect. [note 1]"
        assert body["low_confidence"] is True
        assert body["sources"] == [
            {"note_id": 1, "title": "React Hooks Deep Dive", "source": ""}
        ]

    def test_the_task_records_one_retrieval_attempt(self):
        fake_result = {
            "answer": "yes",
            "docs": [],
            "low_confidence": False,
            "outcome": OUTCOME_AFTER_RETRY,
            "attempts": 2,
            "grades": [GRADE_INSUFFICIENT, GRADE_SUFFICIENT],
            "rewritten_queries": ["talking about hooks"],
            "first_doc_count": 5,
            "final_doc_count": 3,
            "latency_ms": 900,
        }
        with patch(
            "note_generator.rag.chain.answer_question", return_value=fake_result
        ):
            self._post(query="hooks?")

        row = RetrievalAttempt.objects.get()
        assert row.user_id == self.user.id
        assert row.query == "hooks?"
        assert row.first_grade == GRADE_INSUFFICIENT
        assert row.outcome == OUTCOME_AFTER_RETRY
        assert row.attempts == 2
        assert row.retries_used == 1
        assert row.rewritten_queries == ["talking about hooks"]

    def test_search_requires_authentication(self):
        self.client.logout()
        assert self._post().status_code in (401, 403)


class RetrievalStatsCommandTest(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username="stats", password="pw-12345")
        NotePost.objects.create(
            user=self.user,
            youtube_title="React Hooks Deep Dive",
            youtube_link="",
            generated_content="hooks content",
        )

    def _seed(self, first_grade, outcome, attempts, query="a question about hooks"):
        RetrievalAttempt.objects.create(
            user=self.user,
            query=query,
            attempts=attempts,
            first_grade=first_grade,
            grades=[first_grade],
            rewritten_queries=[],
            outcome=outcome,
            first_doc_count=5,
            final_doc_count=5,
            latency_ms=1000,
        )

    def _run(self, *args):
        out = StringIO()
        call_command("retrieval_stats", *args, stdout=out)
        return out.getvalue()

    def test_reports_counts_and_suppresses_the_headline_below_min_sample(self):
        for _ in range(6):
            self._seed(GRADE_SUFFICIENT, OUTCOME_FIRST_PASS, 1)
        for _ in range(3):
            self._seed(GRADE_INSUFFICIENT, OUTCOME_AFTER_RETRY, 2)
        self._seed(GRADE_INSUFFICIENT, OUTCOME_EXHAUSTED, 3)
        self._seed(GRADE_UNPARSEABLE, OUTCOME_FIRST_PASS, 1)

        output = self._run("--days", "1")

        assert "total recorded              11" in output
        assert "excluded as UNPARSEABLE     1" in output
        assert "graded, counted below       10" in output
        # 4 of 10 graded queries failed the first pass, 3 of those recovered.
        assert "insufficient                4 (40.0%" in output
        assert "recovered after 1 retry     3 (75.0%" in output
        assert "still insufficient at cap   1 (25.0%" in output
        # 10 rows is nowhere near quotable, so there is no sentence to copy.
        assert "Sample too small to quote: 10" in output
        assert "Defensible one-sentence claim" not in output

    def test_prints_the_claim_once_the_sample_is_large_enough(self):
        for _ in range(8):
            self._seed(GRADE_SUFFICIENT, OUTCOME_FIRST_PASS, 1)
        self._seed(GRADE_INSUFFICIENT, OUTCOME_AFTER_RETRY, 2)
        self._seed(GRADE_INSUFFICIENT, OUTCOME_EXHAUSTED, 3)

        output = self._run("--days", "1", "--min-sample", "10")

        assert "Defensible one-sentence claim" in output
        assert "1 of 2 queries (50.0%)" in output
        assert "graded insufficient produced a sufficient result" in output
        assert "Sample too small" not in output

    def test_no_rows_in_window_says_so_rather_than_dividing_by_zero(self):
        assert "No retrieval attempts recorded" in self._run("--days", "1")

    def test_rejects_a_bad_since_date(self):
        from django.core.management.base import CommandError

        with self.assertRaises(CommandError):
            self._run("--since", "last-tuesday")
