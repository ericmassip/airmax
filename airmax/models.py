from django.contrib.auth.models import AbstractUser
from django.db import models
from django.db.models import CheckConstraint, Q, UniqueConstraint


class Parameter(models.TextChoices):
    """The six pollutants the source reports. Labels are what the map switcher and legend show."""

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


class User(AbstractUser):
    """Empty today. Swapping the user model once rows exist is a data migration, so it costs nothing now and a great
    deal later."""


class Location(models.Model):
    """A monitoring location the source calls a `locationId`, and the only identity it gives us. There is no sensor id
    anywhere in the stream. Everything here is an attribute of the location itself rather than of any one reading.

    Nothing in this table is a source of truth, it is derived from the stream and can be rebuilt by replaying the raw
    capture.
    """

    id = models.IntegerField(
        primary_key=True
    )  # payload's `locationId` called `id` so Measurement's FK is `location_id`
    name = models.CharField(max_length=100)  # Currently matches `city` in all messages
    city = models.CharField(
        max_length=100
    )  # Will become a geospatial model by itself in future steps
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
    latitude = models.FloatField()  # Where the instrument was for THIS reading, so a mobile
    longitude = models.FloatField()  # location moving never rewrites its own history
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
