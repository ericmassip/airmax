from datetime import datetime as dt, timedelta
from zoneinfo import ZoneInfo

import pytest
from django.contrib.auth import get_user_model
from django.db import connection

BRUSSELS = ZoneInfo("Europe/Brussels")
UTC = ZoneInfo("UTC")


@pytest.mark.django_db
def test_datetimes_are_stored_in_utc_whatever_offset_they_arrive_with():
    """The same Brussels wall-clock reads +01:00 in winter and +02:00 in summer, so a naive
    14:00 is two different instants six months apart. Storage has to collapse both to UTC or
    every window bound near a DST boundary is an hour wrong."""
    User = get_user_model()
    User.objects.create_user(
        username="winter", date_joined=dt(2026, 1, 1, 14, 0, tzinfo=BRUSSELS)
    )
    User.objects.create_user(
        username="summer", date_joined=dt(2026, 7, 1, 14, 0, tzinfo=BRUSSELS)
    )

    stored = list(
        User.objects.order_by("username").values_list("username", "date_joined")
    )

    assert stored == [
        ("summer", dt(2026, 7, 1, 12, 0, tzinfo=UTC)),  # 14:00 +02:00
        ("winter", dt(2026, 1, 1, 13, 0, tzinfo=UTC)),  # 14:00 +01:00
    ]
    # Aware datetimes compare by instant, so the assertion above would hold even if these
    # came back on a Brussels offset. Pin the representation separately.
    assert [value.utcoffset() for _, value in stored] == [timedelta(0), timedelta(0)]


@pytest.mark.django_db
def test_the_column_itself_holds_utc_not_a_local_wall_clock():
    """Reads the value back through SQL rather than the ORM, so the assertion is about what
    PostgreSQL actually stored and not about how Django re-renders it on the way out."""
    get_user_model().objects.create_user(
        username="ann", date_joined=dt(2026, 7, 1, 14, 0, tzinfo=BRUSSELS)
    )

    with connection.cursor() as cursor:
        cursor.execute("select date_joined at time zone 'UTC' from airmax_user")
        naive_utc = cursor.fetchone()[0]

    assert naive_utc == dt(2026, 7, 1, 12, 0)
