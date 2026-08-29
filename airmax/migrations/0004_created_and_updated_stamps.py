import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):
    """Renames `ingested_at` to `created_at` and gives both tables an `updated_at`.

    Hand-written only to skip makemigrations' interactive prompt: the three new columns are
    non-null on tables that already hold rows, so they need a one-off default for the backfill.
    `preserve_default=False` drops it again afterwards, which is what Django itself writes when
    you answer that prompt with `timezone.now`.
    """

    dependencies = [
        ("airmax", "0003_field_widths_and_choices"),
    ]

    operations = [
        migrations.RenameField(
            model_name="measurement",
            old_name="ingested_at",
            new_name="created_at",
        ),
        migrations.AddField(
            model_name="measurement",
            name="updated_at",
            field=models.DateTimeField(auto_now=True),
        ),
        migrations.AddField(
            model_name="location",
            name="created_at",
            field=models.DateTimeField(
                auto_now_add=True, default=django.utils.timezone.now
            ),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="location",
            name="updated_at",
            field=models.DateTimeField(auto_now=True),
        ),
    ]
