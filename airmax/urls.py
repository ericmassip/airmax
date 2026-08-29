from django.urls import path

from airmax.views import Home

app_name = "airmax"

urlpatterns = [
    path("", Home.as_view(), name="home"),
]
