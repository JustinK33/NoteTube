"""
Compute the retrieval grading/retry metrics from real RetrievalAttempt rows.

Usage:
    python manage.py retrieval_stats
    python manage.py retrieval_stats --days 7
    python manage.py retrieval_stats --since 2026-09-01 --min-sample 50

Every percentage is printed next to the raw counts it came from. Below
--min-sample the headline sentence is suppressed, because a percentage over a
handful of queries is not a number worth quoting anywhere.
"""

import re
from datetime import datetime, timedelta

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from note_generator.models import (
    GRADE_INSUFFICIENT,
    GRADE_UNPARSEABLE,
    OUTCOME_AFTER_RETRY,
    OUTCOME_EXHAUSTED,
    OUTCOME_FIRST_PASS,
    NotePost,
    RetrievalAttempt,
)

# mm:ss or h:mm:ss, the "what did they say at 4:12" case.
_TIMESTAMP_RE = re.compile(r"\b\d{1,2}:\d{2}(?::\d{2})?\b")
_WORD_RE = re.compile(r"[a-z0-9']+")
# Too common to count as evidence of vocabulary overlap with a note title.
_STOPWORDS = frozenset(
    """a an and are as at be by can did do does for from he how i in is it its of on
    or she that the their there they this to was were what when where which who why
    will with you your about not""".split()
)


def _pct(part: int, whole: int) -> str:
    """Percent as a string, or 'n/a' when the denominator is zero."""
    if not whole:
        return "n/a"
    return f"{100.0 * part / whole:.1f}%"


def _tokens(text: str) -> set[str]:
    return {w for w in _WORD_RE.findall(text.lower()) if w not in _STOPWORDS}


class Command(BaseCommand):
    help = "Report how often retrieval was graded insufficient and whether retries recovered it"

    def add_arguments(self, parser):
        parser.add_argument(
            "--days",
            type=int,
            default=30,
            help="Window size in days, counting back from now (default 30).",
        )
        parser.add_argument(
            "--since",
            type=str,
            default=None,
            help="Start date as YYYY-MM-DD. Overrides --days.",
        )
        parser.add_argument(
            "--min-sample",
            type=int,
            default=100,
            help="Suppress the headline sentence below this many graded queries (default 100).",
        )

    def handle(self, *args, **options):
        since = self._resolve_since(options)
        min_sample = options["min_sample"]

        rows = list(RetrievalAttempt.objects.filter(created_at__gte=since))
        window = f"since {since:%Y-%m-%d %H:%M}"

        if not rows:
            self.stdout.write(f"No retrieval attempts recorded {window}.")
            return

        unparseable = [r for r in rows if r.first_grade == GRADE_UNPARSEABLE]
        graded = [r for r in rows if r.first_grade != GRADE_UNPARSEABLE]

        self._write_totals(rows, graded, unparseable, window)

        if not graded:
            self.stdout.write(
                self.style.WARNING(
                    "\nEvery attempt in this window was unparseable. Nothing to measure. "
                    "Check the grader prompt against the model's actual output."
                )
            )
            return

        insufficient = [r for r in graded if r.first_grade == GRADE_INSUFFICIENT]
        self._write_outcomes(graded, insufficient)
        self._write_patterns(graded, insufficient)
        self._write_headline(graded, insufficient, min_sample)

    def _resolve_since(self, options):
        if options["since"]:
            try:
                parsed = datetime.strptime(options["since"], "%Y-%m-%d")
            except ValueError:
                raise CommandError(
                    f"--since must be YYYY-MM-DD, got {options['since']!r}"
                )
            return timezone.make_aware(parsed)
        if options["days"] < 1:
            raise CommandError("--days must be at least 1")
        return timezone.now() - timedelta(days=options["days"])

    def _write_totals(self, rows, graded, unparseable, window):
        self.stdout.write(self.style.SUCCESS(f"Retrieval attempts {window}"))
        self.stdout.write(f"  total recorded              {len(rows)}")
        self.stdout.write(
            f"  excluded as UNPARSEABLE     {len(unparseable)} "
            f"({_pct(len(unparseable), len(rows))} of total)"
        )
        self.stdout.write(f"  graded, counted below       {len(graded)}")

    def _write_outcomes(self, graded, insufficient):
        n = len(graded)
        first_pass = sum(1 for r in graded if r.outcome == OUTCOME_FIRST_PASS)
        after_retry = [r for r in graded if r.outcome == OUTCOME_AFTER_RETRY]
        exhausted = sum(1 for r in graded if r.outcome == OUTCOME_EXHAUSTED)

        # attempts == 2 means one retry fixed it, >= 3 means it took two.
        one_retry = sum(1 for r in after_retry if r.attempts == 2)
        two_retries = sum(1 for r in after_retry if r.attempts >= 3)

        self.stdout.write("\nFirst pass")
        self.stdout.write(
            f"  sufficient                  {first_pass} ({_pct(first_pass, n)} of {n})"
        )
        self.stdout.write(
            f"  insufficient                {len(insufficient)} "
            f"({_pct(len(insufficient), n)} of {n})   <- baseline failure rate"
        )

        m = len(insufficient)
        self.stdout.write(f"\nOf the {m} insufficient first passes")
        self.stdout.write(
            f"  recovered after 1 retry     {one_retry} ({_pct(one_retry, m)} of {m})"
        )
        self.stdout.write(
            f"  recovered after 2 retries   {two_retries} ({_pct(two_retries, m)} of {m})"
        )
        self.stdout.write(
            f"  still insufficient at cap   {exhausted} ({_pct(exhausted, m)} of {m})"
        )
        self.stdout.write(
            f"  net recovered by the loop   {len(after_retry)} "
            f"({_pct(len(after_retry), m)} of {m}, {_pct(len(after_retry), n)} of all {n})"
        )

        latencies = sorted(r.latency_ms for r in graded)
        median = latencies[len(latencies) // 2]
        self.stdout.write(
            f"\n  median latency              {median} ms "
            "(semantic cache hits included, so this is not raw model cost)"
        )

    def _write_patterns(self, graded, insufficient):
        """Where the insufficient queries differ from the rest.

        Descriptive only. With a small sample these buckets are a hint about
        what to look at next, not a finding.
        """
        sufficient = [r for r in graded if r.first_grade != GRADE_INSUFFICIENT]
        self.stdout.write("\nQuery patterns (insufficient first pass vs the rest)")

        if not insufficient or not sufficient:
            self.stdout.write("  need both groups populated to compare")
            return

        buckets = [("1-3 words", 1, 3), ("4-8 words", 4, 8), ("9+ words", 9, 10**6)]
        for label, low, high in buckets:
            bad = sum(1 for r in insufficient if low <= len(r.query.split()) <= high)
            ok = sum(1 for r in sufficient if low <= len(r.query.split()) <= high)
            self.stdout.write(
                f"  {label:<24} insufficient {bad} ({_pct(bad, len(insufficient))})"
                f"   sufficient {ok} ({_pct(ok, len(sufficient))})"
            )

        bad_ts = sum(1 for r in insufficient if _TIMESTAMP_RE.search(r.query))
        ok_ts = sum(1 for r in sufficient if _TIMESTAMP_RE.search(r.query))
        self.stdout.write(
            f"  {'contains a timestamp':<24} insufficient {bad_ts} "
            f"({_pct(bad_ts, len(insufficient))})   sufficient {ok_ts} "
            f"({_pct(ok_ts, len(sufficient))})"
        )

        # Vocabulary mismatch: does the query share any word with a note title?
        # Titles are the only per-user text cheap enough to scan here; note
        # bodies live in PGVector, not queryable this way.
        # ponytail: title tokens only, add body overlap if this bucket looks
        # like it is actually explaining the failures.
        titles_by_user: dict[int | None, list[set[str]]] = {}
        for row in graded:
            if row.user_id not in titles_by_user:
                titles_by_user[row.user_id] = [
                    _tokens(t)
                    for t in NotePost.objects.filter(user_id=row.user_id).values_list(
                        "youtube_title", flat=True
                    )
                ]

        def no_title_overlap(row) -> bool:
            q = _tokens(row.query)
            return not any(q & title for title in titles_by_user[row.user_id])

        bad_ov = sum(1 for r in insufficient if no_title_overlap(r))
        ok_ov = sum(1 for r in sufficient if no_title_overlap(r))
        self.stdout.write(
            f"  {'no note-title overlap':<24} insufficient {bad_ov} "
            f"({_pct(bad_ov, len(insufficient))})   sufficient {ok_ov} "
            f"({_pct(ok_ov, len(sufficient))})"
        )

    def _write_headline(self, graded, insufficient, min_sample):
        n, m = len(graded), len(insufficient)
        recovered = sum(1 for r in insufficient if r.outcome == OUTCOME_AFTER_RETRY)

        if n < min_sample:
            self.stdout.write(
                self.style.WARNING(
                    f"\nSample too small to quote: {n} graded queries, threshold is {min_sample}."
                    f"\nThe counts above are real. A percentage off {n} queries is not."
                )
            )
            return

        self.stdout.write(self.style.SUCCESS("\nDefensible one-sentence claim"))
        self.stdout.write(
            f'  "{recovered} of {m} queries ({_pct(recovered, m)}) whose first-pass '
            f"retrieval was graded insufficient produced a sufficient result after a "
            f'rewritten query, across {n} graded queries."'
        )
        self.stdout.write(
            "\n  That says grader judgment, not verified answer correctness. A stronger\n"
            "  claim needs a hand-labeled question set."
        )
