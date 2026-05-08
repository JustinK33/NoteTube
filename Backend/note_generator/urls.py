from django.urls import path
from . import views
from .api_views import NoteSearchView

urlpatterns = [
    path("api/notes/search/", NoteSearchView.as_view(), name="api-notes-search"),
    path("", views.home, name="home"),
    path("index", views.index, name="index"),
    path("login", views.user_login, name="login"),
    path("signup", views.user_signup, name="signup"),
    path("logout", views.user_logout, name="logout"),
    path("generate-notes", views.generate_note, name="generate-notes"),
    path("mp3-to-notes", views.mp3_to_notes, name="mp3-to-notes"),
    path("note-create", views.note_create, name="note-create"),
    path("saved-notes", views.note_list, name="saved-notes"),
    path("note-details/<int:pk>/", views.note_details, name="note-details"),
    path("note-edit/<int:pk>/", views.note_edit, name="note-edit"),
    path("note-delete/<int:pk>/", views.note_delete, name="note-delete"),
    path("note-export/<int:pk>/", views.note_export, name="note-export"),
    path("notion-settings", views.notion_settings, name="notion-settings"),
    path(
        "note-export-notion/<int:pk>/",
        views.note_export_notion,
        name="note-export-notion",
    ),
]
