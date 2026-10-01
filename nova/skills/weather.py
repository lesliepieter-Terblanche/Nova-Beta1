"""Weather: live forecast from Open-Meteo (free, no API key), shown on a weather page and read aloud.

"What's the weather?"            -> your home city (assistant.city in config)
"Will it rain in Durban tomorrow?" -> any place, up to 7 days ahead
"""
from __future__ import annotations

import datetime as dt
import json
import webbrowser

import httpx

from .. import context
from ..config import resolve
from ..tools import register_group, tool

register_group("weather", ["weather", "temperature", "forecast", "rain", "raining", "umbrella", "sunny", "cloudy",
                           "storm", "thunder", "wind", "windy", "hot today", "cold today", "degrees", "uv", "hail",
                           "sunrise", "sunset", "jacket"])

GEO_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

# WMO weather codes -> (description, icon key)
CODES = {
    0: ("clear skies", "clear"), 1: ("mostly clear", "clear"), 2: ("partly cloudy", "partly"), 3: ("overcast", "cloud"),
    45: ("foggy", "fog"), 48: ("freezing fog", "fog"),
    51: ("light drizzle", "drizzle"), 53: ("drizzle", "drizzle"), 55: ("heavy drizzle", "drizzle"),
    56: ("freezing drizzle", "drizzle"), 57: ("heavy freezing drizzle", "drizzle"),
    61: ("light rain", "rain"), 63: ("rain", "rain"), 65: ("heavy rain", "rain"),
    66: ("freezing rain", "rain"), 67: ("heavy freezing rain", "rain"),
    71: ("light snow", "snow"), 73: ("snow", "snow"), 75: ("heavy snow", "snow"), 77: ("snow grains", "snow"),
    80: ("light showers", "showers"), 81: ("showers", "showers"), 82: ("violent showers", "showers"),
    85: ("snow showers", "snow"), 86: ("heavy snow showers", "snow"),
    95: ("thunderstorms", "storm"), 96: ("thunderstorms with hail", "storm"), 99: ("severe thunderstorms with hail", "storm"),
}


def describe(code) -> tuple[str, str]:
    return CODES.get(int(code or 0), ("mixed weather", "partly"))


def geocode(place: str) -> dict:
    r = httpx.get(GEO_URL, params={"name": place, "count": 1, "language": "en", "format": "json"}, timeout=15)
    r.raise_for_status()
    results = r.json().get("results") or []
    if not results:
        # "Roodepoort, South Africa" -> try just the first part
        if "," in place:
            return geocode(place.split(",")[0].strip())
        raise ValueError(f"I couldn't find a place called '{place}'.")
    g = results[0]
    label = ", ".join(x for x in (g.get("name"), g.get("admin1"), g.get("country")) if x)
    return {"name": g["name"], "label": label, "lat": g["latitude"], "lon": g["longitude"]}


def fetch(place: str = "") -> dict:
    """Current conditions, next 24 hours and 7 days for a place (default: home city)."""
    place = (place or (context.cfg.get("assistant") or {}).get("city") or "").strip()
    if not place:
        raise ValueError("Tell me which city, or set your home city in Settings → General.")
    g = geocode(place)
    r = httpx.get(FORECAST_URL, timeout=20, params={
        "latitude": g["lat"], "longitude": g["lon"], "timezone": "auto", "forecast_days": 7,
        "current": "temperature_2m,apparent_temperature,relative_humidity_2m,weather_code,wind_speed_10m,"
                   "wind_gusts_10m,precipitation,is_day",
        "hourly": "temperature_2m,precipitation_probability,weather_code,is_day",
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,"
                 "precipitation_sum,sunrise,sunset,uv_index_max,wind_speed_10m_max",
    })
    r.raise_for_status()
    d = r.json()
    cur, hourly, daily = d["current"], d["hourly"], d["daily"]
    now = cur["time"][:13]
    start = next((i for i, t in enumerate(hourly["time"]) if t[:13] >= now), 0)
    hours = [{"time": hourly["time"][i][11:16], "temp": round(hourly["temperature_2m"][i]),
              "rain": hourly["precipitation_probability"][i] or 0,
              "icon": describe(hourly["weather_code"][i])[1], "day": bool(hourly["is_day"][i])}
             for i in range(start, min(start + 24, len(hourly["time"])))]
    days = []
    for i, date in enumerate(daily["time"]):
        desc, icon = describe(daily["weather_code"][i])
        days.append({"date": date, "name": "Today" if i == 0 else "Tomorrow" if i == 1 else
                     dt.date.fromisoformat(date).strftime("%A"),
                     "desc": desc, "icon": icon, "max": round(daily["temperature_2m_max"][i]),
                     "min": round(daily["temperature_2m_min"][i]),
                     "rain": daily["precipitation_probability_max"][i] or 0,
                     "rain_mm": daily["precipitation_sum"][i] or 0, "uv": daily["uv_index_max"][i],
                     "wind": round(daily["wind_speed_10m_max"][i] or 0),
                     "sunrise": daily["sunrise"][i][11:16], "sunset": daily["sunset"][i][11:16]})
    desc, icon = describe(cur["weather_code"])
    data = {
        "place": g["label"], "short": g["name"], "updated": cur["time"],
        "current": {"temp": round(cur["temperature_2m"]), "feels": round(cur["apparent_temperature"]),
                    "humidity": cur["relative_humidity_2m"], "wind": round(cur["wind_speed_10m"]),
                    "gusts": round(cur.get("wind_gusts_10m") or 0), "desc": desc, "icon": icon,
                    "day": bool(cur["is_day"])},
        "hours": hours, "days": days,
    }
    out = resolve("workspace/weather")
    out.mkdir(parents=True, exist_ok=True)
    (out / "latest.json").write_text(json.dumps(data, indent=1), encoding="utf-8")
    return data


def spoken(data: dict, day_index: int = 0) -> str:
    c, days = data["current"], data["days"]
    d = days[min(day_index, len(days) - 1)]
    if day_index == 0:
        s = (f"Right now in {data['short']} it's {c['temp']} degrees and {c['desc']}"
             + (f", feeling like {c['feels']}" if abs(c["feels"] - c["temp"]) >= 2 else "") + ". "
             f"Today goes up to {d['max']} with a low of {d['min']}")
    else:
        s = f"{d['name']} in {data['short']}: {d['desc']}, between {d['min']} and {d['max']} degrees"
    rain = d["rain"]
    s += (f", and a {rain} percent chance of rain." if rain >= 20 else ", and it should stay dry.")
    if day_index == 0 and len(days) > 1:
        t = days[1]
        s += f" Tomorrow: {t['desc']}, {t['min']} to {t['max']}" + (f", {t['rain']} percent chance of rain." if t["rain"] >= 30 else ".")
    if d.get("uv") and d["uv"] >= 8:
        s += " The UV is very high, so wear sunscreen."
    if d["wind"] >= 40:
        s += f" It'll be windy, up to {d['wind']} kilometres an hour."
    return s


@tool(group="weather")
def get_weather(place: str = "", day: str = "today", show: bool = True) -> str:
    """Get the live weather forecast, open the weather page on screen, and return what to say out loud.
    Use this for ANY weather question (never web_search). Read the returned sentence to the user as your answer.
    Args:
        place: city or town; leave empty for the user's home city
        day: today, tomorrow, or a weekday name like friday
        show: show the weather big on the dashboard (or open the weather page)
    """
    try:
        data = fetch(place)
    except Exception as e:
        return f"ERROR: I couldn't get the weather right now ({e})."
    idx = 0
    want = (day or "today").strip().lower()
    for i, d in enumerate(data["days"]):
        weekday = dt.date.fromisoformat(d["date"]).strftime("%A").lower()
        if want in (d["name"].lower(), d["date"], weekday, weekday[:3]):
            idx = i
            break
    if show:
        from .. import cards
        cards.show("weather", f"Weather · {data['short']}", {"day": idx, "place": data["place"]})
        if not cards.viewer_active():                   # no dashboard open: show the weather page instead
            port = (context.cfg.get("dashboard") or {}).get("port", 8765)
            webbrowser.open(f"http://localhost:{port}/weather?day={idx}")
    return spoken(data, idx)
