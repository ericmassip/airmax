import json
from pathlib import Path

from django.contrib.gis.geos import GEOSGeometry, MultiPolygon
from django.core.management.base import BaseCommand

from airmax.models import City

DEFAULT_PATH = Path("data/be_municipalities.geojson")


class Command(BaseCommand):
    help = "Loads Belgian municipality boundaries from a GeoJSON file."

    def add_arguments(self, parser):
        parser.add_argument("path", nargs="?", type=Path, default=DEFAULT_PATH)

    def handle(self, *args, **options):
        features = json.loads(options["path"].read_text())["features"]

        cities = []
        for feature in features:
            properties = feature["properties"]
            geometry = GEOSGeometry(json.dumps(feature["geometry"]), srid=4326)
            # A handful of cities are multi polygons so setting them all as Polygon's for simplicity, no impact
            geometry = MultiPolygon(geometry, srid=4326)
            cities.append(
                City(
                    refnis=properties["refnis"],
                    name_nl=properties["name_nl"],
                    name_fr=properties["name_fr"],
                    name_de=properties["name_de"],
                    # Statbel leaves this empty for Brussels' 19 municipalities, which belong to no province
                    province=properties["province"] or None,
                    region=properties["region"],
                    geometry=geometry,
                )
            )

        City.objects.bulk_create(
            cities,
            update_conflicts=True,
            update_fields=["name_nl", "name_fr", "name_de", "province", "region", "geometry"],
            unique_fields=["refnis"],
        )
        self.stdout.write(self.style.SUCCESS(f"Loaded {len(cities)} cities from {options['path']}"))
