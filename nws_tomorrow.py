import os
import requests
from datetime import datetime, timezone, timedelta

LAT = 40.7812
LON = -73.9665

DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")
USER_AGENT = "WeatherSignalBot/1.0 (contact: you@example.com)"

def now_utc():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")

def send(msg: str):
    if not DISCORD_WEBHOOK_URL:
        raise RuntimeError("DISCORD_WEBHOOK_URL is not set")
    r = requests.post(DISCORD_WEBHOOK_URL, json={"content": msg}, timeout=20)
    r.raise_for_status()

def parse_iso(dt_str: str) -> datetime:
    if dt_str.endswith("Z"):
        dt_str = dt_str[:-1] + "+00:00"
    return datetime.fromisoformat(dt_str)

def get_periods():
    points = requests.get(
        f"https://api.weather.gov/points/{LAT},{LON}",
        headers={"User-Agent": USER_AGENT, "Accept": "application/geo+json"},
        timeout=20,
    )
    points.raise_for_status()
    forecast_url = points.json()["properties"]["forecast"]

    fc = requests.get(
        forecast_url,
        headers={"User-Agent": USER_AGENT, "Accept": "application/geo+json"},
        timeout=20,
    )
    fc.raise_for_status()
    return fc.json()["properties"]["periods"]

def pick_tomorrow_daytime(periods):
    first_start = parse_iso(periods[0]["startTime"])
    local_today = first_start.date()
    local_tomorrow = local_today + timedelta(days=1)

    for p in periods:
        if not p.get("isDaytime"):
            continue
        start_local_date = parse_iso(p["startTime"]).date()
        if start_local_date == local_tomorrow:
            return p
    raise ValueError("Tomorrow daytime period not found")
9
def main():
    periods = get_periods()
    p = pick_tomorrow_daytime(periods)

    name = p.get("name")
    temp = p.get("temperature")
    short = p.get("shortForecast") or ""

    send(
        f"🌤️ NWS TOMORROW HIGH — Central Park\n"
        f"Period: **{name}**\n"
        f"Forecast high: **{temp}°F**\n"
        f"Conditions: {short}\n"
        f"Time: {now_utc()}"
    )
    print("Posted NWS tomorrow high.")

if __name__ == "__main__":
    main()
