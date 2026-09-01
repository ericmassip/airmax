"""Breaking half of the move to PostGIS: drops the columns the old Lambda writes and renames `Location.city`.

Do not apply this until the SQS consumer has been redeployed to write `point` and `source_city`. Applying it early
fails every insert, and the batch goes back on the queue until the visibility timeout expires. SQS retains the
messages, so an early apply costs a gap rather than data -- but it is still a gap.
"""

import django.contrib.gis.db.models.fields
from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("airmax", "0005_city_and_measurement_point"),
    ]

    operations = [
        migrations.RenameField(
            model_name="location",
            old_name="city",
            new_name="source_city",
        ),
        migrations.RemoveField(model_name="measurement", name="latitude"),
        migrations.RemoveField(model_name="measurement", name="longitude"),
        migrations.AlterField(
            model_name="measurement",
            name="point",
            field=django.contrib.gis.db.models.fields.PointField(srid=4326),
        ),
    ]
