from datetime import datetime as dt
from zoneinfo import ZoneInfo

from django.template import Context, Template

UTC = ZoneInfo("UTC")


def test_stored_utc_renders_as_the_belgian_wall_clock_on_both_sides_of_dst():
    """A station reading is a fact about Belgian air at a Belgian moment, so 12:00Z in July
    has to reach the page as 14:00 and 13:00Z in January as 14:00 — the same local hour, from
    two different instants. Rendering is the only place that conversion happens."""
    template = Template("{{ value|date:'H:i T' }}")

    rendered = [
        template.render(Context({"value": dt(2026, 7, 1, 12, 0, tzinfo=UTC)})),
        template.render(Context({"value": dt(2026, 1, 1, 13, 0, tzinfo=UTC)})),
    ]

    assert rendered == ["14:00 CEST", "14:00 CET"]
