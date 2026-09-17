from django.db import models
from django.contrib.auth.models import User


class NotePost(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    youtube_title = models.CharField(max_length=300)
    youtube_link = models.URLField(blank=True, null=True)  # Optional for MP3 sources
    generated_content = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.youtube_title


class UserProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="profile")
    notion_token = models.CharField(max_length=255, blank=True, default="")
    notion_parent_page_id = models.CharField(max_length=64, blank=True, default="")

    def has_notion_configured(self) -> bool:
        return bool(self.notion_token and self.notion_parent_page_id)

    def __str__(self):
        return f"Profile<{self.user.username}>"


class NoteEmbedding(models.Model):
    """Bookkeeping for a NotePost's PGVector embedding.

    PGVector owns the embedding row itself; this table just lets us look up
    the vector id from a NotePost and skip re-embedding when content is
    unchanged (content_hash match).
    """

    note = models.OneToOneField(
        NotePost, on_delete=models.CASCADE, related_name="embedding"
    )
    vector_id = models.CharField(max_length=64, unique=True)
    content_hash = models.CharField(max_length=64)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Embedding<note={self.note.pk}>"


# Grades the retrieval grader can return. UNPARSEABLE is not a verdict, it means
# the model didn't follow the two-line output contract; the loop treats it as
# SUFFICIENT (fail open) but it's recorded separately so it can be excluded from
# the stats rather than silently counted as a pass.
GRADE_SUFFICIENT = "SUFFICIENT"
GRADE_INSUFFICIENT = "INSUFFICIENT"
GRADE_UNPARSEABLE = "UNPARSEABLE"
GRADE_CHOICES = [
    (GRADE_SUFFICIENT, "Sufficient"),
    (GRADE_INSUFFICIENT, "Insufficient"),
    (GRADE_UNPARSEABLE, "Unparseable"),
]

OUTCOME_FIRST_PASS = "sufficient_first_pass"
OUTCOME_AFTER_RETRY = "sufficient_after_retry"
OUTCOME_EXHAUSTED = "exhausted"
OUTCOME_CHOICES = [
    (OUTCOME_FIRST_PASS, "Sufficient on first pass"),
    (OUTCOME_AFTER_RETRY, "Sufficient after retry"),
    (OUTCOME_EXHAUSTED, "Exhausted retries"),
]


class RetrievalAttempt(models.Model):
    """One row per answered question, recording how retrieval actually went.

    Exists to measure the self-correcting loop instead of guessing at it: how
    often the first pass was graded insufficient, how often a rewritten query
    fixed it, and how often nothing did. `retrieval_stats` reads this table.

    `first_grade` duplicates `grades[0]` on purpose. It's the dimension every
    stat groups by, and an indexed column beats reaching into JSON for it.
    """

    user = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="retrieval_attempts",
    )
    query = models.TextField(help_text="The question as the user asked it.")
    attempts = models.PositiveSmallIntegerField(
        help_text="Retrieval passes made, 1 through 1 + RAG_MAX_RETRIES."
    )
    first_grade = models.CharField(max_length=16, choices=GRADE_CHOICES, db_index=True)
    grades = models.JSONField(default=list, help_text="Grade per pass, in order.")
    rewritten_queries = models.JSONField(
        default=list, help_text="Queries the grader produced, in order."
    )
    outcome = models.CharField(max_length=32, choices=OUTCOME_CHOICES, db_index=True)
    first_doc_count = models.PositiveSmallIntegerField()
    final_doc_count = models.PositiveSmallIntegerField()
    latency_ms = models.PositiveIntegerField()
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]

    @property
    def retries_used(self) -> int:
        return max(self.attempts - 1, 0)

    def __str__(self):
        return f"RetrievalAttempt<{self.outcome} in {self.attempts}>"
