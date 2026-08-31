import json
import logging
from collections.abc import Iterable
from dataclasses import asdict, astuple, dataclass
from datetime import UTC, datetime

from django.db import transaction

from airmax.models import Location, Measurement

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ParsedLocation:
    id: int
    name: str
    city: str
    country: str
    is_mobile: bool
    entity: str
    sensor_type: str

    @classmethod
    def from_payload(cls, payload: dict) -> ParsedLocation:
        return cls(
            id=payload["locationId"],
            name=payload["location"],
            city=payload["city"],
            country=payload["country"],
            is_mobile=payload["isMobile"],
            entity=payload["entity"],
            sensor_type=payload["sensorType"],
        )


@dataclass(frozen=True)
class ParsedMeasurement:
    location: ParsedLocation
    parameter: str
    value: float
    unit: str
    latitude: float
    longitude: float
    is_analysis: bool
    event_time: datetime

    @classmethod
    def from_payload(cls, payload: dict) -> ParsedMeasurement:
        coordinates = payload["coordinates"]
        return cls(
            location=ParsedLocation.from_payload(payload),
            parameter=payload["parameter"],
            value=payload["value"],
            unit=payload["unit"],
            latitude=coordinates["latitude"],
            longitude=coordinates["longitude"],
            is_analysis=payload["isAnalysis"],
            # `date.local` is the only field carrying a real offset, so we convert it to UTC instead of using `date.utc`
            event_time=datetime.fromisoformat(payload["date"]["local"]).astimezone(UTC),
        )


@dataclass(frozen=True)
class UpsertResult:
    """Returns a summary of the inserted/updated objects after a full upsert run"""

    locations_created: int = 0
    locations_updated: int = 0
    measurements_created: int = 0
    measurements_updated: int = 0

    def __add__(self, other):
        return UpsertResult(*(a + b for a, b in zip(astuple(self), astuple(other))))


def parse_measurement_body(body: str) -> ParsedMeasurement:
    """Unwraps the body of one raw SQS message into a measurement

    Example:
        The enclosing message, unescaped and indented for readability. `Body` is the string we are handed. Both inner
        layers arrive as strings, not objects:

            {
              "MessageId": "840454a7-03ac-48aa-a942-491d20c90224",
              "Body": {
                "Type": "Notification",
                "TopicArn": "arn:aws:sns:eu-west-1:...:openaq-simulator",
                "Message": {
                  "locationId": 8752,
                  "location": "Daussoulx",
                  "parameter": "no2",
                  "value": 96.6642121093,
                  "date": {
                    "utc": "2026-08-25 10:50:46.782729",      <- not UTC, see below
                    "local": "2026-08-25 10:50:46.782729+02:00"
                  },
                  "unit": "µg/m³",
                  "coordinates": {"latitude": 50.5181967, "longitude": 4.8886216},
                  "country": "BE",
                  "city": "Daussoulx",
                  "isMobile": false,
                  "isAnalysis": true,
                  "entity": "difficult",
                  "sensorType": "low-cost"
                },
                "Signature": "X/CrzwVOjZHZRooyA24GJBNzmUa/RE69..."
              }
            }

    Returns:
        ParsedMeasurement(
            location=ParsedLocation(id=8752, name="Daussoulx", city="Daussoulx",
                                    country="BE", is_mobile=False,
                                    entity="difficult", sensor_type="low-cost"),
            parameter="no2", value=96.6642121093, unit="µg/m³",
            latitude=50.5181967, longitude=4.8886216, is_analysis=True,
            event_time=datetime(2026, 8, 25, 8, 50, 46, 782729, tzinfo=UTC), <- 08:50 UTC, not 10:50 CET
        )

    NB: `date.utc` is not UTC, it's the local wall clock with the offset chopped off, identical to `date.local`, so we
    use parse `date.local` to UTC instead.

    Raises ValueError on a payload that does not parse.
    """
    return ParsedMeasurement.from_payload(json.loads(json.loads(body)["Message"]))


@transaction.atomic
def upsert_measurements(measurements: Iterable[ParsedMeasurement]) -> UpsertResult:
    """Upserts a batch of parsed measurements and the locations behind them. SQS is at-least-once, so measurements might
    be stored more than once, with or without updates. Therefore, both tables upsert on their natural key, replaying an
    existing message overwrites it with the same values instead of raising or duplicating.

    Both tables are written in key order, which is what keeps concurrent invocations off each other's backs. See the
    comment on the sort below.
    """
    # Deduplicate rows with the same identity (location_id, parameter, event_time) and keep the last one, because
    # Postgres would fail a batch with an `ON CONFLICT DO UPDATE` on the same row twice. Also sort to prevent two
    # concurrent invocations deadlocking each other on airmax_location.
    deduplicated_measurements = {
        (m.location.id, m.parameter, m.event_time): m
        for m in sorted(measurements, key=lambda m: (m.location.id, m.parameter, m.event_time))
    }
    if not deduplicated_measurements:
        return UpsertResult()

    locations_before = Location.objects.count()
    # location_id is the first element of the sort key, so these come out ordered too, with no second sort
    locations = {
        measurement.location.id: Location(**asdict(measurement.location))
        for measurement in deduplicated_measurements.values()
    }
    Location.objects.bulk_create(
        locations.values(),
        update_conflicts=True,
        unique_fields=["id"],
        update_fields=[
            "name",
            "city",
            "country",
            "is_mobile",
            "entity",
            "sensor_type",
            "updated_at",
        ],
    )
    locations_created = Location.objects.count() - locations_before

    measurement_rows = [
        Measurement(
            location_id=measurement.location.id,
            parameter=measurement.parameter,
            event_time=measurement.event_time,
            value=measurement.value,
            unit=measurement.unit,
            latitude=measurement.latitude,
            longitude=measurement.longitude,
            is_analysis=measurement.is_analysis,
        )
        for measurement in deduplicated_measurements.values()
    ]
    measurements_before = Measurement.objects.count()
    Measurement.objects.bulk_create(
        measurement_rows,
        update_conflicts=True,
        unique_fields=["location", "parameter", "event_time"],
        update_fields=["value", "unit", "latitude", "longitude", "is_analysis", "updated_at"],
    )
    measurements_created = Measurement.objects.count() - measurements_before

    result = UpsertResult(
        locations_created=locations_created,
        locations_updated=len(locations) - locations_created,
        measurements_created=measurements_created,
        measurements_updated=len(measurement_rows) - measurements_created,
    )
    log.debug(
        "Stored a batch of %d: %d locations (%d new, %d updated), "
        "%d measurements (%d new, %d updated)",
        len(measurement_rows),
        len(locations),
        result.locations_created,
        result.locations_updated,
        len(measurement_rows),
        result.measurements_created,
        result.measurements_updated,
    )
    return result
