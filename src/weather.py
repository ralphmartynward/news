"""Fetch daily weather for Toulouse from Open-Meteo (free, no API key)."""
from __future__ import annotations

from datetime import date
from typing import Any

import requests

LAT, LON = 43.6047, 1.4442
TIMEOUT   = 8

_WMO_EMOJI = {
    0:  "☀️",
    1:  "🌤️", 2: "🌤️", 3: "☁️",
    45: "🌫️", 48: "🌫️",
    51: "🌦️", 53: "🌦️", 55: "🌦️",
    61: "🌧️", 63: "🌧️", 65: "🌧️",
    71: "❄️",  73: "❄️",  75: "❄️", 77: "❄️",
    80: "🌧️", 81: "🌧️", 82: "🌧️",
    85: "❄️",  86: "❄️",
    95: "⛈️",  96: "⛈️", 99: "⛈️",
}

FRENCH_DAYS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]


def _emoji(code: int) -> str:
    return _WMO_EMOJI.get(code, "🌡️")


_WMO_BUCKET = {
    0: "sunny", 1: "sunny",
    2: "partlycloudy",
    3: "cloudy", 45: "cloudy", 48: "cloudy",
    51: "rainy", 53: "rainy", 55: "rainy",
    61: "rainy", 63: "rainy", 65: "rainy",
    71: "snowy", 73: "snowy", 75: "snowy", 77: "snowy",
    80: "rainy", 81: "rainy", 82: "rainy",
    85: "snowy", 86: "snowy",
    95: "rainy", 96: "rainy", 99: "rainy",
}


def weather_bucket(code: int) -> str:
    """Collapse a WMO weather code into one of: sunny, partlycloudy, cloudy, rainy, snowy.

    code 2 ("partly cloudy" — sun still visible) is kept distinct from
    code 3/45/48 ("overcast"/fog — sky fully grey), since they look quite
    different in a static backdrop photo.
    """
    return _WMO_BUCKET.get(code, "cloudy")


def fetch(days: int = 7) -> list[dict[str, Any]] | None:
    """Return a list of daily forecasts (up to `days`), or None on error.

    Each dict: {"date": date, "emoji": str, "max": int, "min": int}
    """
    try:
        r = requests.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": LAT,
                "longitude": LON,
                "daily": "temperature_2m_max,temperature_2m_min,weathercode",
                "timezone": "Europe/Paris",
                "forecast_days": days,
            },
            timeout=TIMEOUT,
        )
        r.raise_for_status()
        data = r.json()["daily"]
        return [
            {
                "date":  date.fromisoformat(d),
                "code":  int(c),
                "emoji": _emoji(int(c)),
                "max":   round(mx),
                "min":   round(mn),
            }
            for d, c, mx, mn in zip(
                data["time"],
                data["weathercode"],
                data["temperature_2m_max"],
                data["temperature_2m_min"],
            )
        ]
    except Exception as exc:
        print(f"weather: fetch failed — {exc}")
        return None


def current_code() -> int | None:
    """WMO weather code for right now, not the day's aggregate.

    The daily code represents the day's single most significant condition —
    a brief predicted shower can mark the whole day "rainy" even when it's
    sunny the rest of the time. For picking a backdrop image at generation
    time (posted the same morning), current conditions are the better signal.
    """
    try:
        r = requests.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": LAT,
                "longitude": LON,
                "current": "weather_code",
                "timezone": "Europe/Paris",
            },
            timeout=TIMEOUT,
        )
        r.raise_for_status()
        return int(r.json()["current"]["weather_code"])
    except Exception as exc:
        print(f"weather: current_code fetch failed — {exc}")
        return None


def today_line() -> str:
    """Single-line summary for the newsletter: '☀️ 33° / 19° min'"""
    forecasts = fetch(days=1)
    if not forecasts:
        return ""
    f = forecasts[0]
    return f"{f['emoji']} {f['max']}° max · {f['min']}° min"


def weekend_lines() -> tuple[str, str]:
    """Return (saturday_line, sunday_line) for the carousel cover."""
    forecasts = fetch(days=7)
    if not forecasts:
        return "", ""
    by_date = {f["date"]: f for f in forecasts}
    today = date.today()
    wd = today.weekday()  # Mon=0
    days_to_sat = (5 - wd) % 7
    sat = today.__class__.fromordinal(today.toordinal() + days_to_sat)
    sun = today.__class__.fromordinal(sat.toordinal() + 1)
    def fmt(d: date) -> str:
        f = by_date.get(d)
        if not f:
            return ""
        day = FRENCH_DAYS[d.weekday()][:3].capitalize()
        return f"{day} {f['max']}°"
    return fmt(sat), fmt(sun)
