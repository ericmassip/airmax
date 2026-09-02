from django.urls import path

from airmax.views.map.map_view import MapView

app_name = "airmax"

urlpatterns = [
    path("", MapView.as_view(), name="map"),
]
