import json
import logging
from collections.abc import Iterable
from dataclasses import astuple, dataclass
from datetime import UTC, datetime

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ParsedLocation:
    id: int
    name: str
    source_city: str
    country: str
    is_mobile: bool
    entity: str
    sensor_type: str

    @classmethod
    def from_payload(cls, payload: dict) -> ParsedLocation:
        return cls(
            id=payload["locationId"],
            name=payload["location"],
            # The village the stream claims. Where the reading actually belongs is decided by its coordinates.
            source_city=payload["city"],
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

    @property
    def ewkt(self) -> str:
        """(Extended Well-Known Text) The position as PostGIS takes it directly, with longitude first:
        ST_MakePoint is (x, y)."""
        return f"SRID=4326;POINT({self.longitude} {self.latitude})"


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
            location=ParsedLocation(id=8752, name="Daussoulx", source_city="Daussoulx",
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


# `created_at` and `updated_at` default to now(), except inside DO UPDATE where `created_at` is not updated
#
# `xmax = 0` is only true for insert rows, that's how we count inserted vs updated
LOCATION_SQL = """
INSERT INTO airmax_location
    (id, name, source_city, country, is_mobile, entity, sensor_type, created_at, updated_at)
VALUES {rows}
ON CONFLICT (id) DO UPDATE SET
    name = EXCLUDED.name,
    source_city = EXCLUDED.source_city,
    country = EXCLUDED.country,
    is_mobile = EXCLUDED.is_mobile,
    entity = EXCLUDED.entity,
    sensor_type = EXCLUDED.sensor_type,
    updated_at = now()
RETURNING (xmax = 0)
"""
LOCATION_ROW = "(%s, %s, %s, %s, %s, %s, %s, now(), now())"

# The city is resolved here rather than at read time to save resources when querying the db in the future with millions
# of measurements. Also, we can then keep the geospatial library outside the Lambda.
#
# The CTE exists so the point can be named once and used twice: stored, and asked which city contains it
MEASUREMENT_SQL = """
WITH measurement_batch (location_id, parameter, event_time, value, unit, point, is_analysis) AS (
    VALUES {rows}
)
INSERT INTO airmax_measurement
    (location_id, parameter, event_time, value, unit, point, city_id, is_analysis, created_at, updated_at)
SELECT
    m.location_id, m.parameter, m.event_time, m.value, m.unit, m.point,
    (SELECT c.refnis FROM airmax_city c WHERE ST_Contains(c.geometry, m.point) LIMIT 1),
    m.is_analysis, now(), now()
FROM measurement_batch m
ON CONFLICT (location_id, parameter, event_time) DO UPDATE SET
    value = EXCLUDED.value,
    unit = EXCLUDED.unit,
    point = EXCLUDED.point,
    city_id = EXCLUDED.city_id,
    is_analysis = EXCLUDED.is_analysis,
    updated_at = now()
RETURNING (xmax = 0)
"""
MEASUREMENT_ROW = "(%s, %s, %s, %s, %s, ST_GeomFromEWKT(%s), %s)"


def upsert_measurements(connection, measurements: Iterable[ParsedMeasurement]) -> UpsertResult:
    """Upserts a batch of parsed measurements and the locations behind them, in one transaction. SQS is at-least-once,
    so measurements might be stored more than once, with or without updates. Therefore, both tables upsert on their
    natural key, replaying an existing message overwrites it with the same values instead of raising or duplicating.

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

    with connection.cursor() as cursor:
        # location_id is the first element of the sort key, so these come out ordered too, with no second sort
        locations = {m.location.id: m.location for m in deduplicated_measurements.values()}
        location_params = [field for location in locations.values() for field in astuple(location)]
        location_rows = ", ".join([LOCATION_ROW] * len(locations))
        cursor.execute(LOCATION_SQL.format(rows=location_rows), location_params)
        locations_created = sum(inserted for (inserted,) in cursor.fetchall())

        measurement_params = []
        for measurement in deduplicated_measurements.values():
            measurement_params += [
                measurement.location.id,
                measurement.parameter,
                measurement.event_time,
                measurement.value,
                measurement.unit,
                measurement.ewkt,
                measurement.is_analysis,
            ]
        measurement_rows = ", ".join([MEASUREMENT_ROW] * len(deduplicated_measurements))
        cursor.execute(MEASUREMENT_SQL.format(rows=measurement_rows), measurement_params)
        measurements_created = sum(inserted for (inserted,) in cursor.fetchall())

    result = UpsertResult(
        locations_created=locations_created,
        locations_updated=len(locations) - locations_created,
        measurements_created=measurements_created,
        measurements_updated=len(deduplicated_measurements) - measurements_created,
    )
    log.debug(
        "Stored a batch of %d: %d locations (%d new, %d updated), %d measurements (%d new, %d updated)",
        len(deduplicated_measurements),
        len(locations),
        result.locations_created,
        result.locations_updated,
        len(deduplicated_measurements),
        result.measurements_created,
        result.measurements_updated,
    )
    return result
