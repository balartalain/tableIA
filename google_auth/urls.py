from django.urls import path

from google_auth import views

app_name = "google_auth"

urlpatterns = [
    path("login/", views.login, name="login"),
    path("login-page/", views.login_page, name="login_page"),
    path("callback/", views.callback, name="callback"),
    path("status/", views.status, name="status"),
    path("disconnect/", views.disconnect, name="disconnect"),
    path("logout/", views.logout_view, name="logout"),
]
