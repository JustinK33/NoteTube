from django.contrib import admin
from .models import NotePost, RetrievalAttempt

# Register your models here.
admin.site.register(NotePost)


@admin.register(RetrievalAttempt)
class RetrievalAttemptAdmin(admin.ModelAdmin):
    """Read-only: these rows are the measurement, so hand-editing them would
    quietly change a number that gets quoted elsewhere."""

    list_display = ("created_at", "outcome", "first_grade", "attempts", "latency_ms")
    list_filter = ("outcome", "first_grade", "created_at")
    search_fields = ("query",)
    date_hierarchy = "created_at"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
