from django.urls import path
from . import views
 
urlpatterns = [
    path("", views.index, name="chat_index"),
    path("api/", views.chat_api, name="chat_api"),
    path("api/feedback/", views.chat_feedback, name="chat_feedback"),
    path("api/matchups/", views.chat_matchups, name="chat_matchups"),
    path("api/match-result/", views.chat_match_result, name="chat_match_result"),
    path("api/death/", views.chat_death, name="chat_death"),
    path("api/scoreboard/", views.chat_scoreboard_ocr, name="chat_scoreboard_ocr"),
    path("api/sync/", views.chat_sync, name="chat_sync"),
    path("api/sync/code/", views.chat_sync_code, name="chat_sync_code"),
    path("api/sync/link/", views.chat_sync_link, name="chat_sync_link"),
    path("api/sync/unlink/", views.chat_sync_unlink, name="chat_sync_unlink"),
]
 