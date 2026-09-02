"""Script to build the simplified municipality boundaries the map draws. The generated file becomes a static file that
the browser caches, that way we take some weight off the db shoulders by not querying it every time for the city
polygons. This is safe to run because city boundaries rarely change. However, if the municipalities geojson changes,
this script must be run again."""

import json
from pathlib import Path

from django.core.management.base import BaseCommand
from django.db import connection

DEFAULT_PATH = Path("static/geo/be_municipalities.geojson")

DEFAULT_TOLERANCE = 0.005  # See https://postgis.net/docs/ST_CoverageSimplify.html
# Statbel does not ship a clean coverage, sometimes neighbours describe the same border with different vertices, so we
# decided to use ST_CoverageClean to show smooth borders on the map.
DEFAULT_GAP_WIDTH = 0.0005  # See https://postgis.net/docs/ST_CoverageClean.html
# About a metre. The simplification moves every vertex much further than that, so more digits store noise.
COORDINATE_DECIMALS = 5

BOUNDARIES_SQL = """
WITH cleaned AS (
    SELECT refnis, ST_CoverageClean(geometry, %s) OVER () AS geometry
    FROM airmax_city
), simplified AS (
    SELECT refnis, ST_CoverageSimplify(geometry, %s) OVER () AS geometry
    FROM cleaned
)
SELECT refnis, ST_AsGeoJSON(ST_Multi(geometry), %s)
FROM simplified
ORDER BY refnis
"""


def count_vertices(coordinates):
    if isinstance(coordinates[0], (int, float)):
        return 1
    return sum(count_vertices(part) for part in coordinates)


class Command(BaseCommand):
    help = "Writes the simplified municipality boundaries the map draws, as a static GeoJSON file."

    def add_arguments(self, parser):
        parser.add_argument("path", nargs="?", type=Path, default=DEFAULT_PATH)
        parser.add_argument("--tolerance", type=float, default=DEFAULT_TOLERANCE)
        parser.add_argument("--gap-width", type=float, default=DEFAULT_GAP_WIDTH)

    def handle(self, *args, **options):
        with connection.cursor() as cursor:
            cursor.execute(
                BOUNDARIES_SQL, [options["gap_width"], options["tolerance"], COORDINATE_DECIMALS]
            )
            rows = cursor.fetchall()

        # `refnis` is the only property because names are already in the map payload
        features = [
            {
                "type": "Feature",
                "properties": {"refnis": refnis},
                "geometry": json.loads(geometry),
            }
            for refnis, geometry in rows
        ]
        document = {"type": "FeatureCollection", "features": features}

        path = options["path"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(document, separators=(",", ":")))

        vertices = sum(count_vertices(feature["geometry"]["coordinates"]) for feature in features)
        self.stdout.write(
            self.style.SUCCESS(
                f"Wrote {len(features)} municipalities to {path} "
                f"({path.stat().st_size / 1024:.0f} KB, {vertices} vertices, "
                f"tolerance {options['tolerance']}, gap width {options['gap_width']})"
            )
        )
