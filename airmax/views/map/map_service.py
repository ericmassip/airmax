import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from django.conf import settings
from django.db.models import Count, Sum
from django.db.models.functions import Trunc
from django.utils import timezone

from airmax.models import City, Measurement, Parameter

log = logging.getLogger(__name__)

# The six pollutants never change without a migration, so their positions are fixed
INDEX_BY_PARAMETER = {parameter: index for index, parameter in enumerate(Parameter.values)}


@dataclass(frozen=True)
class MapCity:
    """A municipality as the map labels it, without its geometry. The boundaries are a separate payload: they are the
    same 565 shapes on every load, and they weigh far more than the readings do."""

    refnis: int
    name: str


@dataclass(frozen=True)
class MapData:
    """Everything the frontend needs to paint every municipality for any position of the time slider. Rolling the
    buckets into a window is the client's job and lives in the JS. It sums the totals and the counts and only then
    divides, e.g. a city with one reading of 10 in one hour and forty of 50 in the next comes out at 49, not 30."""

    window_hours: int  # Whole buckets, so a window is always a whole number of them
    span_days: int
    span_start: datetime  # Start of the oldest bucket. `hours` are whole hours after this.
    now: datetime
    cities: list[MapCity]  # Every municipality, including the ones that are empty
    parameters: list[str]
    hours: list[int]
    city_indexes: list[int]
    parameter_indexes: list[int]
    totals: list[float]
    counts: list[int]

    @property
    def hours_in_span(self) -> int:
        """The first bucket is hour 0. The last bucket is hour `span_days * 24`. The axis includes both of them.
        Therefore, the number of buckets is one more than the number of hours."""
        return self.span_days * 24 + 1

    @property
    def latest_window_is_empty(self) -> bool:
        return not self.hours or self.hours[-1] < self.hours_in_span - self.window_hours

    @property
    def newest_bucket_start(self) -> datetime | None:
        if self.hours:
            return self.span_start + timedelta(hours=self.hours[-1])


def get_map_data(window_hours: int | None = None, span_days: int | None = None) -> MapData:
    window_hours = settings.AIRMAX_WINDOW_HOURS if window_hours is None else window_hours
    span_days = settings.AIRMAX_SPAN_DAYS if span_days is None else span_days

    now = timezone.now()
    span_start = (
        (now - timedelta(days=span_days)).astimezone(UTC).replace(minute=0, second=0, microsecond=0)
    )  # Truncated so the oldest bucket is a whole hour

    # Every city regardless of it having data or not. Cities with no data are greyed out.
    cities = [
        MapCity(refnis=refnis, name=name)
        for refnis, name in City.objects.order_by("name_nl").values_list("refnis", "name_nl")
    ]

    data = MapData(
        window_hours=window_hours,
        span_days=span_days,
        span_start=span_start,
        now=now,
        cities=cities,
        parameters=list(Parameter.values),
        **_get_hourly_buckets(
            span_start, {city.refnis: index for index, city in enumerate(cities)}
        ),
    )
    log.debug(
        "City map data: %d buckets across %d of %d municipalities over %d days, opening %dh window %s",
        len(data.hours),
        len(set(data.city_indexes)),
        len(data.cities),
        span_days,
        window_hours,
        "empty" if data.latest_window_is_empty else "populated",
    )
    return data


def _get_hourly_buckets(span_start: datetime, index_by_refnis: dict[int, int]) -> dict:
    """Returns one bucket for each city, parameter and hour.

    A bucket sums the readings since `span_start`. The query is a GROUP BY on the city foreign key. It does not read a
    geometry. The ingest Lambda already found the municipality of each reading with ST_Contains. A bucket keeps a total
    and a count, no average. The frontend adds `window_hours` buckets together, and then it divides. An average of
    averages wouldn't be correct, because it gives a quiet hour the same weight as a busy hour.

    Example:
        Aalst (41002) reports pm25 twice at 08:00 as 12.0 and 14.0. Aalst reports no2 once at 09:15 as 30.0.
        Brugge (31005) reports pm25 once at 09:30 as 8.0.
        The value of `span_start` is 08:00.

        {
            "hours":             [   0,    1,    1],   <- whole hours after `span_start` -> [8h, 9h, 9h]
            "city_indexes":      [   0,    1,    0],   <- index into `cities` -> [Aalst, Brugge, Aalst]
            "parameter_indexes": [   4,    4,    1],   <- index into `parameters` -> [pm25, pm25, no2]
            "totals":            [26.0,  8.0, 30.0],   <- rounded totals
            "counts":            [   2,    1,    1],   <- counts of readings
        }

        Brugge is before Aalst at 09:00, because 31005 is before 41002.
    """
    buckets = (
        Measurement.objects.filter(event_time__gte=span_start)
        .exclude(
            city__isnull=True
        )  # Reject measurements that did not fall within a Belgian municipality
        .annotate(hour=Trunc("event_time", "hour", tzinfo=UTC))
        .values("city_id", "parameter", "hour")
        .annotate(total=Sum("value"), readings=Count("id"))
        .order_by("hour", "city_id", "parameter")
    )

    hours, city_indexes, parameter_indexes, totals, counts = [], [], [], [], []
    for bucket in buckets:
        hours.append(int((bucket["hour"] - span_start) / timedelta(hours=1)))
        city_indexes.append(index_by_refnis[bucket["city_id"]])
        parameter_indexes.append(INDEX_BY_PARAMETER[bucket["parameter"]])
        totals.append(round(bucket["total"], 2))
        counts.append(bucket["readings"])

    return {
        "hours": hours,
        "city_indexes": city_indexes,
        "parameter_indexes": parameter_indexes,
        "totals": totals,
        "counts": counts,
    }
