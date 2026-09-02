"""The colour bands the map paints stations with.

Five of the six pollutants are banded on the **2025 revised EAQI** (ETC HE Report 2024/17, Table 5.2, in force since 3
July 2025), not the 2017 set the throwaway prototype uses. The revision is WHO-2021-aligned and much stricter: NO₂ "
Good" moved from ≤40 to ≤10 µg/m³.

CO is in no European index at all — not the EAQI, not BelAQI, not the UK DAQI — so it gets a scale of its own,
deliberately not an index and deliberately not the EAQI hues. Its three boundaries are all published in mg/m³ natively,
so nothing here is converted or invented: 4 (WHO 2021 AQG, and the EU limit from 2030), 7 (WHO interim target 1), 10
(WHO 8-hour guideline, and today's EU limit).

The palette is the EEA's own, lifted from the index app's CSS and data.js — the EEA publishes no colour specification,
so production code is as close to a source as exists.
"""

from dataclasses import dataclass

from airmax.models import PARAMETER_UNITS, Parameter


@dataclass(frozen=True)
class Band:
    """One step of a scale. `ink` is the text colour that clears 4.5:1 on `colour`."""

    label: str
    colour: str
    ink: str
    lower: float
    upper: float | None  # None on the top band, which is open-ended

    @property
    def range_label(self) -> str:
        """What the legend prints beside the swatch, so a colour never has to be guessed at."""
        return f"> {self.lower:g}" if self.upper is None else f"{self.lower:g}–{self.upper:g}"


@dataclass(frozen=True)
class Scale:
    """The bands for one parameter, plus what the legend has to say about where they come from."""

    caption: str
    source: str
    bands: list[Band]

    def band_index(self, value: float) -> int:
        """Which band a reading falls in. The top band catches everything above the last bound."""
        for index, band in enumerate(self.bands):
            if band.upper is None or value <= band.upper:
                return index
        return len(self.bands) - 1


# label, fill, text colour on that fill
EAQI_PALETTE = [
    ("Good", "#50F0E6", "#000000"),
    ("Fair", "#50CCAA", "#000000"),
    ("Moderate", "#F0E641", "#000000"),
    ("Poor", "#FF5050", "#000000"),
    ("Very poor", "#960032", "#FFFFFF"),
    ("Extremely poor", "#7D2181", "#FFFFFF"),
]

# Blue-grey rather than the EAQI hues, so nobody reads a CO marker as an index band. Each step
# is named for the line the reading has crossed, which is where its defensibility lives.
CO_PALETTE = [
    ("Below WHO 24-hour guideline", "#DCE6EC", "#000000"),
    ("Above WHO 24-hour guideline", "#93B4C6", "#000000"),
    ("Above WHO interim target 1", "#43708B", "#FFFFFF"),
    ("Above EU 8-hour limit value", "#1F4557", "#FFFFFF"),
]

# Upper bound of every band but the last. µg/m³ for the five EAQI pollutants, mg/m³ for CO.
EAQI_THRESHOLDS = {
    Parameter.PM25: (5, 15, 50, 90, 140),
    Parameter.PM10: (15, 45, 120, 195, 270),
    Parameter.NO2: (10, 25, 60, 100, 150),
    Parameter.O3: (60, 100, 120, 160, 180),
    Parameter.SO2: (20, 40, 125, 190, 275),
}
CO_THRESHOLDS = (4, 7, 10)

EAQI_CAPTION = "2025 European Air Quality Index"
EAQI_SOURCE = "EEA / ETC HE Report 2024/17, Table 5.2 · 1-hour reference time"
CO_CAPTION = "WHO and EU reference values — not an index"
CO_SOURCE = "WHO Global Air Quality Guidelines 2021 · Directives 2008/50/EC and (EU) 2024/2881"


def _bands(thresholds: tuple[float, ...], palette: list[tuple[str, str, str]]) -> list[Band]:
    bounds: list[float | None] = [0.0, *thresholds, None]
    return [
        Band(label=label, colour=colour, ink=ink, lower=bounds[index], upper=bounds[index + 1])
        for index, (label, colour, ink) in enumerate(palette)
    ]


NO_DATA = Band(label="Not reported here", colour="#6F6F6F", ink="#FFFFFF", lower=0, upper=None)

SCALES = {
    **{
        parameter: Scale(EAQI_CAPTION, EAQI_SOURCE, _bands(thresholds, EAQI_PALETTE))
        for parameter, thresholds in EAQI_THRESHOLDS.items()
    },
    Parameter.CO: Scale(CO_CAPTION, CO_SOURCE, _bands(CO_THRESHOLDS, CO_PALETTE)),
}


def parameter_payload(parameter: str) -> dict:
    """The scale as the map's JS reads it: the legend rows and what `bandOf` needs to place a value"""
    return {
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
