from django.urls import path
from . import views

urlpatterns = [
    path('', views.home, name='home'),
    path('index', views.index, name='index'),
    path('login', views.user_login, name='login'),
    path('signup', views.user_signup, name='signup'),
    path('logout', views.user_logout, name='logout'),
    path('generate-notes', views.generate_note, name='generate-notes'),
    path('saved-notes', views.note_list, name='saved-notes'),
    path('note-details/<int:pk>/', views.note_details, name='note-details'),
]
