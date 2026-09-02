from datetime import datetime as dt
from datetime import timedelta
from zoneinfo import ZoneInfo

import pytest
from django.urls import reverse

from airmax.models import User
from airmax.views.map.map_service import MapCity, MapData
from airmax.views.map.map_view import get_map_payload

UTC = ZoneInfo("UTC")
NOW = dt(2026, 8, 30, 12, 0, tzinfo=UTC)


def a_map_data(**overrides):
    fields = {
        "window_hours": 3,
        "span_days": 3,
        "span_start": NOW - timedelta(days=3),
        "now": NOW,
        "cities": [MapCity(41002, "Aalst"), MapCity(31005, "Brugge")],
        "parameters": ["co", "no2", "o3", "pm10", "pm25", "so2"],
        "hours": [0, 1, 1],
        "city_indexes": [0, 1, 0],
        "parameter_indexes": [4, 4, 1],
        "totals": [26.0, 8.0, 30.0],
        "counts": [2, 1, 1],
    }
    return MapData(**{**fields, **overrides})


def test_the_payload_ships_the_buckets_as_they_are_and_keeps_refnis_an_integer():
    """The JS joins the boundaries to the payload on `refnis`, and the GeoJSON side is a number. A string here would
    make `11001 === "11001"` false and paint every municipality grey."""
    payload = get_map_payload(a_map_data())

    assert payload["cities"] == [[41002, "Aalst"], [31005, "Brugge"]]
    assert [
        payload["hours"],
        payload["cityIndexes"],
        payload["parameterIndexes"],
        payload["totals"],
        payload["counts"],
    ] == [[0, 1, 1], [0, 1, 0], [4, 4, 1], [26.0, 8.0, 30.0], [2, 1, 1]]


def test_the_payload_carries_the_axis_the_slider_moves_along():
    payload = get_map_payload(a_map_data())

    assert [payload["windowHours"], payload["hoursInSpan"]] == [3, 3 * 24 + 1]
    assert payload["spanStart"] == (NOW - timedelta(days=3)).timestamp()
    assert payload["now"] == NOW.timestamp()
    assert payload["boundariesUrl"] == "/static/geo/be_municipalities.geojson"


def test_the_payload_defaults_to_pm25_and_ships_every_scale():
    payload = get_map_payload(a_map_data())

    assert payload["parameter"] == "pm25"
    assert [parameter["name"] for parameter in payload["parameters"]] == [
        "co",
        "no2",
        "o3",
        "pm10",
        "pm25",
        "so2",
    ]
    assert payload["parameters"][4]["unit"] == "µg/m³"
    assert payload["noData"] == {"label": "Not reported here", "colour": "#6F6F6F"}


@pytest.mark.django_db
def test_the_page_needs_a_login(client):
    response = client.get(reverse("airmax:map"))

    assert response.status_code == 302
    assert response.url.startswith(reverse("login"))


@pytest.mark.django_db
def test_the_page_embeds_the_payload_for_the_js_to_read(client):
    client.force_login(User.objects.create_user("eric"))

    response = client.get(reverse("airmax:map"))

    assert response.status_code == 200
    assert b'id="map-payload"' in response.content
    assert b"be_municipalities.geojson" in response.content
