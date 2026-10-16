from django.urls import path

from google_auth import views

app_name = "google_auth"

urlpatterns = [
    path("login/", views.login, name="login"),
    path("callback/", views.callback, name="callback"),
    path("status/", views.status, name="status"),
    path("disconnect/", views.disconnect, name="disconnect"),
]
