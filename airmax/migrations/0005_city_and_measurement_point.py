"""Additive half of the move to PostGIS. Everything here is safe to run against a database the currently deployed
Lambda is still writing to: no column it writes is renamed or dropped. The breaking half is 0006, which must not run
until that Lambda has been redeployed.
"""

import django.contrib.gis.db.models.fields
import django.db.models.deletion
from django.contrib.postgres.operations import CreateExtension
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("airmax", "0004_created_and_updated_stamps"),
    ]

    operations = [
        # Needs a superuser, which our `airmax` role has on RDS via rds_superuser. The app's own least-privilege role
        # would not. See: https://docs.djangoproject.com/en/6.1/ref/contrib/gis/install/postgis/
        CreateExtension("postgis"),
        migrations.CreateModel(
            name="City",
            fields=[
                ("refnis", models.CharField(max_length=5, primary_key=True, serialize=False)),
                ("name_nl", models.CharField(max_length=100)),
                ("name_fr", models.CharField(max_length=100)),
                ("name_de", models.CharField(max_length=100)),
                ("province", models.CharField(blank=True, max_length=100, null=True)),
                ("region", models.CharField(max_length=100)),
                ("geometry", django.contrib.gis.db.models.fields.MultiPolygonField(srid=4326)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={"verbose_name_plural": "cities"},
        ),
        migrations.AddField(
            model_name="measurement",
            name="point",
            field=django.contrib.gis.db.models.fields.PointField(null=True, srid=4326),
        ),
        # One UPDATE rather than a RunPython loop: this is a bulk column transform over every row in the table, and
        # pulling 100k+ rows through the ORM to write each one back individually would take minutes instead of seconds.
        # ST_MakePoint takes (x, y) -> longitude first. Getting that backwards puts every Belgian station in Somalia.
        migrations.RunSQL(
            sql="""UPDATE airmax_measurement
                   SET point = ST_SetSRID(ST_MakePoint(longitude, latitude), 4326)
                   WHERE point IS NULL""",
            reverse_sql="""UPDATE airmax_measurement
                           SET latitude = ST_Y(point), longitude = ST_X(point)
                           WHERE point IS NOT NULL""",
        ),
        migrations.AddField(
            model_name="measurement",
            name="city",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="measurements",
                to="airmax.city",
            ),
        ),
        migrations.AddIndex(
            model_name="measurement",
            index=models.Index(fields=["city", "event_time"], name="measurement_city_time_idx"),
        ),
    ]
