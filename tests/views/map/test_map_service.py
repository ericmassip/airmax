from datetime import datetime as dt
from datetime import timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest

from airmax.models import Location, Measurement
from airmax.views.map.map_service import get_map_data

UTC = ZoneInfo("UTC")

# A fixed anchor rather than the wall clock: every reading below is placed relative to it, so
# the arithmetic under test is the same run at any hour of any day.
NOW = dt(2026, 8, 30, 12, 0, tzinfo=UTC)


@pytest.fixture
def frozen_clock():
    """`get_map_data` reads the clock itself. Patching it beats passing an instant in, which would
    put an argument in the signature that only tests ever use."""
    with patch("django.utils.timezone.now", return_value=NOW):
        yield


def a_location(id, name):
    return Location.objects.create(
        id=id,
        name=name,
        city=name,
        country="BE",
        is_mobile=False,
        entity="research",
        sensor_type="reference",
    )


def a_reading(location, minutes_ago, value, *, latitude=50.0, longitude=4.0, parameter="pm25"):
    return Measurement.objects.create(
        location=location,
        parameter=parameter,
        event_time=NOW - timedelta(minutes=minutes_ago),
        value=value,
        unit="mg/m³" if parameter == "co" else "µg/m³",
        latitude=latitude,
        longitude=longitude,
        is_analysis=False,
    )


@pytest.mark.django_db
def test_the_series_carries_every_reading_in_event_time_order(frozen_clock):
    """The client aggregates from this and nothing else, so the contract is the series: right
    rows, right order, right values. The rows are written in one instant and dated across four
    hours, so anything ordering on ingestion time would hand the client the wrong sequence."""
    location = a_location(1, "Daussoulx")
    for minutes_ago, value in [(30, 10.0), (240, 99.5), (90, 20.0), (150, 30.0)]:
        a_reading(location, minutes_ago, value)

    data = get_map_data(window_hours=3)

    # Offsets are seconds after the oldest reading, which is the one 240 minutes back.
    assert list(zip(data.times, data.values)) == [
        (0, 99.5),
        (90 * 60, 30.0),
        (150 * 60, 20.0),
        (210 * 60, 10.0),
    ]
    assert data.measurements_in_window == 3  # the 240-minute-old row is outside a 3-hour window
    assert data.newest_event_time == NOW - timedelta(minutes=30)


@pytest.mark.django_db
def test_the_page_carries_the_last_history_days_and_nothing_older(frozen_clock):
    """The cutoff is counted back from now, so it is exactly what it says: the last three days.
    Anything older is simply absent, however recent it is relative to the rest of the store."""
    stale = a_location(1, "Ambleve")
    a_reading(stale, minutes_ago=4 * 24 * 60, value=1.0)
    recent = a_location(2, "Daussoulx")
    a_reading(recent, minutes_ago=3 * 24 * 60 + 1, value=2.0)  # a minute too old
    a_reading(recent, minutes_ago=3 * 24 * 60 - 1, value=3.0)
    a_reading(recent, minutes_ago=26 * 60, value=4.0)

    data = get_map_data(window_hours=3, history_days=3)

    assert data.values == [3.0, 4.0]
    # A station whose every reading fell outside is not shipped as an empty marker.
    assert [station.name for station in data.stations] == ["Daussoulx"]


@pytest.mark.django_db
def test_a_store_whose_readings_are_all_older_than_the_limit_yields_an_empty_page(frozen_clock):
    """The consequence of counting back from now, stated rather than discovered: the captured
    fixture is a replay that ages, and once its newest reading falls outside the limit the page
    has nothing at all — no stations, no last known values, a blank map. `AIRMAX_HISTORY_DAYS`
    is the knob that keeps it in range."""
    a_reading(a_location(1, "Daussoulx"), minutes_ago=4 * 24 * 60, value=12.0)

    assert get_map_data(window_hours=3, history_days=3).stations == []
    assert get_map_data(window_hours=3, history_days=5).values == [12.0]


@pytest.mark.django_db
def test_an_empty_window_still_hands_over_every_station_and_its_history(frozen_clock):
    """The path the demo runs on: the newest reading in the capture is already a day old, so
    the live window is empty and stays empty. The page must still receive every station and
    every reading, because the map draws last known values, not the window."""
    a_reading(a_location(1, "Daussoulx"), minutes_ago=26 * 60, value=12.0)
    moving = a_location(2, "Petit-Fays")
    a_reading(moving, minutes_ago=30 * 60, value=7.0, latitude=51.1, longitude=3.3)
    a_reading(moving, minutes_ago=25 * 60, value=8.0, latitude=51.2, longitude=3.4)

    data = get_map_data(window_hours=3)

    assert data.window_is_empty
    assert data.measurements_in_window == 0
    assert data.time_since_newest_measurement == timedelta(hours=25)
    assert len(data.times) == 3
    assert [station.name for station in data.stations] == ["Daussoulx", "Petit-Fays"]
    # Coordinates live on the measurement, not the location, so a station that moved is drawn
    # where its newest reading was taken rather than where its first one was.
    assert (data.stations[1].latitude, data.stations[1].longitude) == (51.2, 3.4)
    # The parallel arrays have to stay aligned, or every marker lands on the wrong station.
    assert list(zip(data.stations_at, data.values)) == [(1, 7.0), (0, 12.0), (1, 8.0)]
