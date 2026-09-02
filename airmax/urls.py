from django.urls import path

from airmax.views.map.city_map_view import CityMapView
from airmax.views.map.map_view import MapView

app_name = "airmax"

urlpatterns = [
    path("", MapView.as_view(), name="map"),
    path("cities/", CityMapView.as_view(), name="city-map"),
]
