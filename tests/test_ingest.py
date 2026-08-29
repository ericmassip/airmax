import json
from datetime import datetime as dt
from io import StringIO
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from django.core.management import call_command

from airmax.ingest import ParsedLocation, ParsedMeasurement, parse_message
from airmax.models import Location, Measurement

UTC = ZoneInfo("UTC")

# Three messages copied verbatim off the queue — signatures, doubled envelope and all. The
# capture itself is 100 MB and gitignored, so this is the committed slice of it.
FIXTURE = Path(__file__).parent / "fixtures" / "sqs_messages.jsonl"


def test_a_verbatim_captured_message_unwraps_through_both_envelopes_to_its_fields():
    message = json.loads(FIXTURE.read_text().splitlines()[0])

    parsed = parse_message(message)

    assert parsed == ParsedMeasurement(
        location=ParsedLocation(
            id=8752,
            name="Daussoulx",
            city="Daussoulx",
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
        # date.local reads 10:50:46.782729+02:00. date.utc carries the identical digits with
        # the offset stripped, so believing its name would file this reading two hours late.
        event_time=dt(2026, 8, 25, 8, 50, 46, 782729, tzinfo=UTC),
    )


@pytest.mark.django_db
def test_loading_the_same_capture_twice_leaves_the_row_counts_unchanged():
    """SQS is at-least-once and we re-run the loader by hand, so this is the property most
    likely to be silently wrong — and the one that would quietly double every average."""
    call_command("load_fixture", str(FIXTURE), stdout=StringIO())
    call_command("load_fixture", str(FIXTURE), stdout=StringIO())

    assert [Location.objects.count(), Measurement.objects.count()] == [3, 3]
