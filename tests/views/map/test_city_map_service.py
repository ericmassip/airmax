from datetime import datetime as dt
from datetime import timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest
from django.contrib.gis.geos import MultiPolygon, Point, Polygon

from airmax.models import City, Location, Measurement
from airmax.views.map.city_map_service import CityMapData, MapCity, get_city_map_data

UTC = ZoneInfo("UTC")

# A whole hour, so a reading placed a round number of minutes back lands in a bucket we can name.
NOW = dt(2026, 8, 30, 12, 0, tzinfo=UTC)
# The default span is 3 days -> 73 buckets, hour 0 to hour 72, and the newest of them holds NOW.
LATEST_HOUR = 3 * 24


@pytest.fixture
def frozen_clock():
    """`get_city_map_data` reads the clock itself. Patching it beats passing an instant in, which would put an argument
    in the signature that only tests ever use."""
    with patch("django.utils.timezone.now", return_value=NOW):
        yield


def a_city(refnis, name):
    return City.objects.create(
        refnis=refnis,
        name_nl=name,
        name_fr=name,
        name_de=name,
        province="Provincie Antwerpen",
        region="Vlaams Gewest",
        # The service reads the names and the foreign key and never the shape, so one square serves every city here.
        geometry=MultiPolygon(Polygon.from_bbox((4.0, 50.0, 5.0, 51.0)), srid=4326),
    )


def a_location(id, name):
    return Location.objects.create(
        id=id,
        name=name,
        source_city=name,
        country="BE",
        is_mobile=False,
        entity="research",
        sensor_type="reference",
    )


def a_reading(location, city, minutes_ago, value, parameter="pm25"):
    return Measurement.objects.create(
        location=location,
        parameter=parameter,
        event_time=NOW - timedelta(minutes=minutes_ago),
        value=value,
        unit="mg/m³" if parameter == "co" else "µg/m³",
        point=Point(4.5, 50.5, srid=4326),
        city=city,
        is_analysis=False,
    )


def a_city_map_data(hours):
    """The dataclass on its own, for the arithmetic that never touches the database."""
    return CityMapData(
        window_hours=3,
        span_days=3,
        span_start=NOW - timedelta(days=3),
        now=NOW,
        cities=[],
        parameters=[],
        hours=hours,
        city_indexes=[],
        parameter_indexes=[],
        totals=[],
        counts=[],
    )


@pytest.mark.django_db
def test_two_stations_in_the_same_city_and_hour_collapse_into_one_bucket(frozen_clock):
    """The whole point of the city map: Aalst is one shape on the map however many stations sit inside it. The bucket
    carries the total and the count rather than the mean, so the client can add three of these up and only then divide.
    """
    aalst = a_city(41002, "Aalst")
    a_reading(a_location(1, "Aalst Noord"), aalst, minutes_ago=30, value=10.0)
    a_reading(a_location(2, "Aalst Zuid"), aalst, minutes_ago=50, value=30.0)

    data = get_city_map_data()

    assert [data.hours, data.city_indexes, data.totals, data.counts] == [
        [LATEST_HOUR - 1],
        [0],
        [10.0 + 30.0],
        [2],
    ]


@pytest.mark.django_db
def test_readings_in_different_hours_stay_in_separate_buckets(frozen_clock):
    aalst = a_city(41002, "Aalst")
    location = a_location(1, "Aalst Noord")
    a_reading(location, aalst, minutes_ago=30, value=10.0)  # 11:30
    a_reading(location, aalst, minutes_ago=90, value=20.0)  # 10:30

    data = get_city_map_data()

    assert [data.hours, data.totals, data.counts] == [
        [LATEST_HOUR - 2, LATEST_HOUR - 1],
        [20.0, 10.0],
        [1, 1],
    ]


@pytest.mark.django_db
def test_every_municipality_is_listed_even_when_it_never_reported(frozen_clock):
    """Brugge has no station of its own but still has to be drawn in the "no data" grey, or the map would have holes
    where the quiet parts of the country are."""
    aalst = a_city(41002, "Aalst")
    a_city(31005, "Brugge")
    a_reading(a_location(1, "Aalst Noord"), aalst, minutes_ago=30, value=10.0)

    data = get_city_map_data()

    assert data.cities == [MapCity(41002, "Aalst"), MapCity(31005, "Brugge")]
    assert [data.city_indexes, data.counts] == [[0], [1]]


@pytest.mark.django_db
def test_a_reading_that_belongs_to_no_municipality_is_left_out(frozen_clock):
    """A reading lands with no city when its point falls outside every Belgian municipality, or when it arrived before
    the boundaries were loaded. Real data, but nowhere on this map to draw it."""
    aalst = a_city(41002, "Aalst")
    a_reading(a_location(1, "Aalst Noord"), aalst, minutes_ago=30, value=10.0)
    a_reading(a_location(2, "Somewhere at sea"), None, minutes_ago=30, value=20.0)

    data = get_city_map_data()

    assert [data.totals, data.counts] == [[10.0], [1]]


@pytest.mark.django_db
def test_the_span_starts_on_a_whole_hour_and_older_readings_are_left_out(frozen_clock):
    aalst = a_city(41002, "Aalst")
    location = a_location(1, "Aalst Noord")
    a_reading(location, aalst, minutes_ago=72 * 60, value=10.0)  # exactly the oldest bucket
    a_reading(location, aalst, minutes_ago=73 * 60, value=20.0)  # an hour before it

    data = get_city_map_data()

    assert [data.hours, data.totals] == [[0], [10.0]]


@pytest.mark.django_db
def test_each_parameter_is_bucketed_on_its_own(frozen_clock):
    """Two pollutants measured at the same place in the same hour are two buckets. Averaging across parameters would be
    meaningless — they are different substances on different scales."""
    aalst = a_city(41002, "Aalst")
    location = a_location(1, "Aalst Noord")
    a_reading(location, aalst, minutes_ago=30, value=10.0, parameter="pm25")
    a_reading(location, aalst, minutes_ago=30, value=20.0, parameter="no2")

    data = get_city_map_data()

    # `parameters` is co, no2, o3, pm10, pm25, so2, and the buckets come back ordered by parameter
    assert [data.parameters[index] for index in data.parameter_indexes] == ["no2", "pm25"]
    assert [data.totals, data.counts] == [[20.0, 10.0], [1, 1]]


def test_the_newest_bucket_start_is_the_hour_of_the_newest_reading_or_nothing():
    assert [a_city_map_data(hours).newest_bucket_start for hours in [[], [LATEST_HOUR - 1]]] == [
        None,
        NOW - timedelta(hours=1),
    ]


def test_the_opening_window_covers_the_newest_hours_and_nothing_older():
    """With 73 buckets on the axis and a 3-hour window, the window the map opens on covers hours 70, 71 and 72. Hour 69
    is one bucket too old, and no buckets at all is empty rather than an error: the bursts are 6 hours apart, so they
    miss most windows entirely."""
    newest_hours = [[], [LATEST_HOUR - 3], [LATEST_HOUR - 2]]

    assert [a_city_map_data(hours).latest_window_is_empty for hours in newest_hours] == [
        True,
        True,
        False,
    ]
