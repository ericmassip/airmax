from django.contrib.auth.mixins import LoginRequiredMixin
from django.views.generic import TemplateView

from airmax.models import PARAMETER_UNITS, Parameter
from airmax.views.map.aqi import NO_DATA, SCALES
from airmax.views.map.map_service import MapData, get_map_data

DEFAULT_PARAMETER = Parameter.PM25


def get_map_payload(data: MapData) -> dict:
    return {
        "parameter": DEFAULT_PARAMETER,
        "windowSeconds": int(data.window_hours * 3600),
        "epoch": data.epoch.timestamp(),
        "now": data.now.timestamp(),
        "noData": {"label": NO_DATA.label, "colour": NO_DATA.colour},
        "parameters": [
            {
                "name": parameter,
                "label": Parameter(parameter).label,
                "unit": PARAMETER_UNITS[parameter],
                "caption": SCALES[parameter].caption,
                "source": SCALES[parameter].source,
                "bands": [
                    {
                        "label": band.label,
                        "colour": band.colour,
                        "ink": band.ink,
                        "range": band.range_label,
                        "upper": band.upper,
                    }
                    for band in SCALES[parameter].bands
                ],
            }
            for parameter in data.parameters
        ],
        "stations": [
            [station.name, station.latitude, station.longitude] for station in data.stations
        ],
        "t": data.times,
        "s": data.stations_at,
        "p": data.parameters_at,
        "v": data.values,
    }


class MapView(LoginRequiredMixin, TemplateView):
    """Leaflet initialises the map from the returned JSON data. The first paint contains all the data it needs so there
    is no request that follows it, every interaction happens on the browser after first load."""

    template_name = "airmax/map.html"

    def get_context_data(self, **kwargs):
        data = get_map_data()
        return super().get_context_data(
            data=data,
            map_payload=get_map_payload(data),
            **kwargs,
        )
