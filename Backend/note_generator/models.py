from django.db import models
from django.contrib.auth.models import User


# Create your models here.
class NotePost(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    youtube_title = models.CharField(max_length=300)
    youtube_link = models.URLField(blank=True, null=True)  # Optional for MP3 sources
    generated_content = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.youtube_title


class UserProfile(models.Model):
    user = models.OneToOneField(
        User, on_delete=models.CASCADE, related_name="profile"
    )
    notion_token = models.CharField(max_length=255, blank=True, default="")
    notion_parent_page_id = models.CharField(max_length=64, blank=True, default="")

    def has_notion_configured(self) -> bool:
        return bool(self.notion_token and self.notion_parent_page_id)

    def __str__(self):
        return f"Profile<{self.user.username}>"
