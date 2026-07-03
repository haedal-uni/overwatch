from django.urls import path
from . import views
 
urlpatterns = [
    path("", views.index, name="chat_index"),
    path("api/", views.chat_api, name="chat_api"),
    path("api/feedback/", views.chat_feedback, name="chat_feedback"),
    path("api/stt/", views.chat_stt, name="chat_stt"),
]
 