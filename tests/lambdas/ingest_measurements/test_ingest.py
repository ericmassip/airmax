import json
from datetime import datetime as dt
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from django.contrib.gis.geos import MultiPolygon, Polygon
from django.db import connection

from airmax.lambdas.ingest_measurements.ingest import (
    ParsedLocation,
    ParsedMeasurement,
    parse_measurement_body,
    upsert_measurements,
)
from airmax.models import City, Location, Measurement

UTC = ZoneInfo("UTC")

SQS_MESSAGES_FIXTURE = Path(__file__).parent / "fixtures" / "sqs_messages.jsonl"


def parsed_fixture_measurements():
    return [
        parse_measurement_body(json.loads(line)["Body"])
        for line in SQS_MESSAGES_FIXTURE.read_text().splitlines()
    ]


def test_a_verbatim_captured_message_unwraps_through_both_envelopes_to_its_fields():
    message = json.loads(SQS_MESSAGES_FIXTURE.read_text().splitlines()[0])

    parsed = parse_measurement_body(message["Body"])

    assert parsed == ParsedMeasurement(
        location=ParsedLocation(
            id=8752,
            name="Daussoulx",
            source_city="Daussoulx",
            country="BE",
            is_mobile=False,
            entity="difficult",
            sensor_type="low-cost",
        ),
        parameter="no2",
        value=96.6642121093,
        unit="µg/m³",
        latitude=50.5181967,
        longitude=4.8886216,
        is_analysis=True,
        event_time=dt(2026, 8, 25, 8, 50, 46, 782729, tzinfo=UTC),
    )


@pytest.mark.django_db
def test_upserting_the_same_capture_twice_leaves_the_row_counts_unchanged():
    measurements = parsed_fixture_measurements()
    connection.ensure_connection()

    first = upsert_measurements(connection.connection, measurements)
    second = upsert_measurements(connection.connection, measurements)

    assert [Location.objects.count(), Measurement.objects.count()] == [3, 3]
    assert [first.measurements_created, first.measurements_updated] == [3, 0]
    assert [second.measurements_created, second.measurements_updated] == [0, 3]


@pytest.mark.django_db
def test_a_measurement_is_attributed_to_the_city_whose_boundary_contains_it():
    # A square 0.1 degrees either side of the Daussoulx reading, synthetic rather than the real Statbel outline so the
    # numbers here are ones you can check by eye. Wide enough to hold that point and nothing else in the fixture.
    longitude, latitude = 4.8886216, 50.5181967
    boundary = Polygon.from_bbox((longitude - 0.1, latitude - 0.1, longitude + 0.1, latitude + 0.1))
    City.objects.create(
        refnis=92094,
        name_nl="Namen",
        name_fr="Namur",
        name_de="Namur",
        province="Provincie Namen",
        region="Waals Gewest",
        geometry=MultiPolygon(boundary, srid=4326),
    )
    connection.ensure_connection()

    upsert_measurements(connection.connection, parsed_fixture_measurements())

    # Only the Daussoulx reading falls inside the square. The other two are 50+ km away, and land with no city rather
    # than failing the write -> backfill_cities picks them up once the real boundaries are loaded.
    assert list(
        Measurement.objects.order_by("location_id").values_list("location_id", "city_id")
    ) == [(4895, None), (7003, None), (8752, 92094)]


@pytest.mark.django_db
def test_measurements_are_written_with_no_cities_loaded_at_all():
    """Ingestion must not depend on the boundaries being loaded first, or a fresh database can never catch up."""
    connection.ensure_connection()

    result = upsert_measurements(connection.connection, parsed_fixture_measurements())

    assert result.measurements_created == 3
    assert Measurement.objects.filter(city__isnull=True).count() == 3
