from django.contrib.auth.mixins import LoginRequiredMixin
from django.templatetags.static import static
from django.views.generic import TemplateView

from airmax.models import Parameter
from airmax.views.map.aqi import NO_DATA, parameter_payload
from airmax.views.map.city_map_service import CityMapData, get_city_map_data

DEFAULT_PARAMETER = Parameter.PM25
# Written by `manage.py build_city_boundaries`. Served as a plain static file so the browser caches it across loads,
# which is the point of not inlining it: it is the same 359 KB every time and outweighs the readings many times over.
BOUNDARIES_PATH = "geo/be_municipalities.geojson"


def get_city_map_payload(data: CityMapData) -> dict:
    """The five bucket lists go over as they are, and the JS sums them into a window itself. Cities are
    `[refnis, name]` pairs, and `refnis` stays an integer because the GeoJSON properties are integers -> the JS joins
    the two with a plain `===`."""
    return {
        "parameter": DEFAULT_PARAMETER,
        "windowHours": data.window_hours,
        "hoursInSpan": data.hours_in_span,
        "spanStart": data.span_start.timestamp(),
        "now": data.now.timestamp(),
        "boundariesUrl": static(BOUNDARIES_PATH),
        "noData": {"label": NO_DATA.label, "colour": NO_DATA.colour},
        "parameters": [parameter_payload(parameter) for parameter in data.parameters],
        "cities": [[city.refnis, city.name] for city in data.cities],
        "hours": data.hours,
        "cityIndexes": data.city_indexes,
        "parameterIndexes": data.parameter_indexes,
        "totals": data.totals,
        "counts": data.counts,
    }


class CityMapView(LoginRequiredMixin, TemplateView):
    """The choropleth: every municipality painted by its window average. Same contract as `MapView`, the page ships one
    JSON payload and every slider move after that is computed in the browser. The boundaries are the one thing the
    page fetches on its own, see `BOUNDARIES_PATH`."""

    template_name = "airmax/city_map.html"

    def get_context_data(self, **kwargs):
        data = get_city_map_data()
        return super().get_context_data(
            data=data,
            map_payload=get_city_map_payload(data),
            **kwargs,
        )
