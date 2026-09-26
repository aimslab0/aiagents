from django.urls import path

from . import views
from .prompt_enhancer import enhance

app_name = "chats"
urlpatterns = [
    path("enhance-prompt/", enhance, name="enhance_prompt"),
    path("chats/clear/", views.clear_chats, name="clear"),
    path("chats/<int:chat_id>/rename/", views.rename_chat, name="rename"),
    path("chats/<int:chat_id>/delete/", views.delete_chat, name="delete"),
    path("research/<int:query_id>/rate/", views.rate_research, name="rate"),
    path("research/<int:query_id>/retry/", views.retry, name="retry"),
    path("evaluations/", views.evaluations, name="evaluations"),
    path("evaluations/<int:case_id>/run/", views.run_evaluation, name="run_evaluation"),
    path("", views.dashboard, name="dashboard"),
    path("chats/new/", views.new_chat, name="new"),
    path("questions/", views.submit_question, name="submit"),
    path("chats/<int:chat_id>/", views.dashboard, name="detail"),
    path("chats/<int:chat_id>/questions/", views.submit_question, name="submit_to_chat"),
]
