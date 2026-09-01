from django.contrib.gis.db import models


class City(models.Model):
    """One of the 565 Belgian municipalities, keyed by its official REFNIS code. Downloaded from
    https://statbel.fgov.be/en/open-data/municipalities-2025 and dissolved per municipality with a python script."""

    refnis = models.CharField(max_length=5, primary_key=True)
    # All three official names stored, choosing which one to show is a display decision
    name_nl = models.CharField(max_length=100)
    name_fr = models.CharField(max_length=100)
    name_de = models.CharField(max_length=100)
    # Brussels' 19 municipalities belong to no province so nullable. `region` is always set so group by that instead.
    province = models.CharField(max_length=100, null=True, blank=True)
    region = models.CharField(max_length=100)
    # MultiPolygon rather than Polygon: 5 of the 565 are genuinely multipart, and Baarle-Hertog is 26 separate
    # fragments scattered inside the Netherlands.
    geometry = models.MultiPolygonField(srid=4326)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = "cities"

    def __str__(self):
        return f"{self.name_nl} ({self.refnis})"
