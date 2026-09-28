from django.urls import path
from . import views
 
urlpatterns = [
    path("", views.index, name="chat_index"),
    path("api/", views.chat_api, name="chat_api"),
    path("api/feedback/", views.chat_feedback, name="chat_feedback"),
    path("api/matchups/", views.chat_matchups, name="chat_matchups"),
    path("api/scoreboard/", views.chat_scoreboard_ocr, name="chat_scoreboard_ocr"),
]
 