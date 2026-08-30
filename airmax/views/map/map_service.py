import bisect
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from django.conf import settings
from django.utils import timezone

from airmax.models import Measurement, Parameter

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Station:
    """A location as the map draws it. Coordinates come from the location's newest measurement rather than off the
    location row, because sensors might be mobile."""

    id: int
    name: str
    latitude: float
    longitude: float


@dataclass(frozen=True)
class MapData:
    """All the data the frontend client needs to render the full map visualisation"""

    window_hours: float
    history_days: float
    epoch: datetime  # `times` are whole seconds after this
    now: datetime  # Passed in rather than read per property because four things ask it so it must be constant
    stations: list[Station]
    parameters: list[str]
    times: list[int]
    stations_at: list[int]  # index into `stations`
    parameters_at: list[int]  # index into `parameters`
    values: list[float]

    @property
    def newest_event_time(self) -> datetime | None:
        if self.times:
            return self.epoch + timedelta(seconds=self.times[-1])

    @property
    def time_since_newest_measurement(self) -> timedelta | None:
        if self.newest_event_time is not None:
            return self.now - self.newest_event_time

    @property
    def measurements_in_window(self) -> int:
        start = self._offset(self.now - timedelta(hours=self.window_hours))
        return len(self.times) - bisect.bisect_left(self.times, start)

    @property
    def window_is_empty(self) -> bool:
        return self.measurements_in_window == 0

    def _offset(self, moment: datetime) -> int:
        return int((moment - self.epoch).total_seconds())


def get_map_data(window_hours: float | None = None, history_days: float | None = None) -> MapData:
    window_hours = settings.AIRMAX_WINDOW_HOURS if window_hours is None else window_hours
    history_days = settings.AIRMAX_HISTORY_DAYS if history_days is None else history_days

    now = timezone.now()
    measurements_data = _get_measurements_data(now - timedelta(days=history_days), now)
    data = MapData(
        window_hours=window_hours, history_days=history_days, now=now, **measurements_data
    )
    log.debug(
        "Map data: %d readings across %d stations over %s days, "
        "%d of them inside the live %sh window",
        len(data.times),
        len(data.stations),
        history_days,
        data.measurements_in_window,
        window_hours,
    )
    return data


def _get_measurements_data(start_time: datetime, end_time: datetime) -> dict:
    """
    Reads and processes measurement data within the given time range. The function extracts information about stations,
    parameters, and measurement values, organizing them into a structured dictionary format for frontend use.

    Parameters:
        start_time (datetime): The start of the time range
        end_time (datetime): The end of the time range (inclusive)

    Returns:
        dict:
            - epoch (datetime): The oldest timestamp of the measurements, used as a reference epoch.
            - stations (list[Station]): A list of distinct Station objects representing the locations of measurements.
            - parameters (list[Any]): A list of all possible parameters for the measurements.
            - times (list[int]): A list of time offsets (in seconds) from the epoch for each measurement.
            - stations_at (list[int]): A list of station indices corresponding to the stations where the measurements were taken.
            - parameters_at (list[int]): A list of parameter indices corresponding to the parameters measured.
            - values (list[float]): A list of rounded measurement values.

    Example:
        Three readings — Aalst reporting pm25 at 08:00 and no2 at 08:30, Brugge pm25 at 09:00:

        {
            "epoch": datetime(2026, 8, 28, 8, 0, tzinfo=UTC),   <- the oldest of the three
            "stations": [Station(id=12, name="Aalst", latitude=50.94, longitude=4.04),
                         Station(id=7, name="Brugge", latitude=51.21, longitude=3.22)],
            "parameters": ["co", "no2", "o3", "pm10", "pm25", "so2"],
            "times":         [   0, 1800, 3600],   <- seconds after `epoch`
            "stations_at":   [   0,    0,    1],   <- index into `stations`
            "parameters_at": [   4,    1,    4],   <- index into `parameters`
            "values":        [12.5, 30.0,  8.0],
        }

        The last four are parallel: column 0 reads as "the station at `stations[0]`, Aalst, measured `parameters[4]`,
        pm25, as 12.5 at `epoch` + 0s".
    """
    measurements_qs = Measurement.objects.filter(event_time__range=(start_time, end_time))

    # One distinct station for each measurement in the given window. Stations with no measurements will not have markers.
    stations = [
        Station(id=id, name=name, latitude=latitude, longitude=longitude)
        for id, name, latitude, longitude in measurements_qs.order_by("location_id", "-event_time")
        .distinct("location_id")
        .values_list("location_id", "location__name", "latitude", "longitude")
    ]
    stations.sort(
        key=lambda station: station.name
    )  # Order doesn't matter, it's just for readability
    station_index = {station.id: index for index, station in enumerate(stations)}

    parameters = list(Parameter.values)
    parameter_index = {parameter: index for index, parameter in enumerate(parameters)}

    measurements = list(
        measurements_qs.order_by("event_time").values_list(
            "location_id", "parameter", "event_time", "value"
        )
    )
    oldest_event_time = measurements[0][2] if measurements else timezone.now()

    times, stations_at, parameters_at, values = [], [], [], []
    for location_id, parameter, event_time, value in measurements:
        # Every time is an offset value from the oldest event time, recovered in js with 'new Date((D.epoch + offset) * 1000)'
        times.append(int((event_time - oldest_event_time).total_seconds()))
        stations_at.append(station_index[location_id])
        parameters_at.append(parameter_index[parameter])
        values.append(round(value, 2))

    return {
        "epoch": oldest_event_time,
        "stations": stations,
        "parameters": parameters,
        "times": times,
        "stations_at": stations_at,
        "parameters_at": parameters_at,
        "values": values,
    }
