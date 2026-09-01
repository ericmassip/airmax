from django.contrib.gis.db import models
from django.db.models import CheckConstraint, Q, UniqueConstraint

from airmax.models.city import City


class Parameter(models.TextChoices):
    CO = "co", "CO"
    NO2 = "no2", "NO₂"
    O3 = "o3", "O₃"
    PM10 = "pm10", "PM10"
    PM25 = "pm25", "PM2.5"
    SO2 = "so2", "SO₂"


class Unit(models.TextChoices):
    MILLIGRAMS_PER_M3 = "mg/m³", "mg/m³"
    MICROGRAMS_PER_M3 = "µg/m³", "µg/m³"


# The units seen until 09-2026, never mixed within a parameter. A new pollutant will need a migration, and a parameter
# with a non-matching unit will raise an error.
PARAMETER_UNITS = {
    Parameter.CO: Unit.MILLIGRAMS_PER_M3,
    Parameter.NO2: Unit.MICROGRAMS_PER_M3,
    Parameter.O3: Unit.MICROGRAMS_PER_M3,
    Parameter.PM10: Unit.MICROGRAMS_PER_M3,
    Parameter.PM25: Unit.MICROGRAMS_PER_M3,
    Parameter.SO2: Unit.MICROGRAMS_PER_M3,
}


class Location(models.Model):
    """A monitoring location the source calls a `locationId`, and the only identity it gives us. There is no sensor id
    anywhere in the stream. Everything here is an attribute of the location itself rather than of any one reading."""

    id = models.IntegerField(
        primary_key=True
    )  # payload's `locationId` called `id` so Measurement's FK is `location_id`
    name = models.CharField(max_length=100)  # Currently matches `source_city` in all messages
    # The city name the location message claims, but not a municipality. We keep it as raw source data but never
    # aggregate on it. See Measurement.city for where a reading actually belongs.
    source_city = models.CharField(max_length=100)
    country = models.CharField(max_length=2)  # Alpha-2 country code
    is_mobile = models.BooleanField()
    entity = models.CharField(max_length=100)
    sensor_type = models.CharField(max_length=100)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.name} ({self.id})"


class Measurement(models.Model):
    """One reading of one pollutant at one location at one moment. (location, parameter, event_time) is the actual
    identity. SQS is at-least-once, so the same reading will arrive more than once. When that happens, the measurement
    will be upserted.
    """

    location = models.ForeignKey(Location, on_delete=models.CASCADE, related_name="measurements")
    parameter = models.CharField(max_length=10, choices=Parameter)
    event_time = models.DateTimeField()
    value = models.FloatField()
    unit = models.CharField(max_length=10, choices=Unit)
    point = models.PointField(srid=4326)
    # Resolved at ingest time from `point` to save query time when reading
    city = models.ForeignKey(
        City, on_delete=models.SET_NULL, related_name="measurements", null=True, blank=True
    )
    is_analysis = models.BooleanField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            UniqueConstraint(
                fields=["location", "parameter", "event_time"],
                name="measurement_natural_key",
            ),
            CheckConstraint(
                condition=Q(
                    *(Q(parameter=str(p), unit=str(u)) for p, u in PARAMETER_UNITS.items()),
                    _connector=Q.OR,
                ),
                name="measurement_unit_matches_parameter",
            ),
        ]
        indexes = [
            models.Index(fields=["event_time"], name="measurement_event_time_idx"),
        ]

    def __str__(self):
        return f"{self.location_id} {self.parameter} {self.event_time:%Y-%m-%d %H:%M} {self.value}{self.unit}"
