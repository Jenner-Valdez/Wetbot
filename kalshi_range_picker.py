import math
import os
import time
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
tz = ZoneInfo("America/New_York")


import requests

# ======================
# CONFIG
# ======================

LAT = 40.7812
LON = -73.9665

SERIES_TICKER = "KXHIGHNY"

DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")

POLL_SECONDS = 10 * 60  # every 10 minutes

# Balanced rules
MIN_EDGE = 0.08
MAX_SPREAD = 0.12
MIN_LIQUIDITY = 50

SIGMA_F = 2.0

NWS_USER_AGENT = "WeatherSignalBot/1.0 (contact: you@example.com)"
KALSHI_USER_AGENT = "WeatherSignalBot/1.0"

KALSHI_BASE = "https://api.elections.kalshi.com/trade-api/v2"
KALSHI_EVENTS_PATH = "/events"
KALSHI_MARKETS_PATH = "/markets"


# ======================
# HELPERS
# ======================
def now_utc_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")


def send_discord(msg: str) -> None:
    if not DISCORD_WEBHOOK_URL:
        raise RuntimeError("DISCORD_WEBHOOK_URL is not set")
    r = requests.post(DISCORD_WEBHOOK_URL, json={"content": msg}, timeout=20)
    r.raise_for_status()


def parse_iso(dt_str: str) -> datetime:
    if dt_str.endswith("Z"):
        dt_str = dt_str[:-1] + "+00:00"
    return datetime.fromisoformat(dt_str)


# ======================
# NWS: TOMORROW HIGH (MEAN)
# ======================
def get_nws_periods():
    points = requests.get(
        f"https://api.weather.gov/points/{LAT},{LON}",
        headers={"User-Agent": NWS_USER_AGENT, "Accept": "application/geo+json"},
        timeout=20,
    )
    points.raise_for_status()
    forecast_url = points.json()["properties"]["forecast"]

    fc = requests.get(
        forecast_url,
        headers={"User-Agent": NWS_USER_AGENT, "Accept": "application/geo+json"},
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

    raise ValueError("Tomorrow daytime period not found in NWS periods")


def get_tomorrow_high_mean():
    periods = get_nws_periods()
    p = pick_tomorrow_daytime(periods)
    name = p.get("name")
    high = int(p.get("temperature"))
    short = p.get("shortForecast") or ""
    return name, high, short


# ======================
# KALSHI: AUTO-DETECT NEXT UNSETTLED EVENT (FIXED)
# ======================
from zoneinfo import ZoneInfo

def kalshi_get_tomorrow_event_ticker(series_ticker: str) -> str:
    tz = ZoneInfo("America/New_York")
    tomorrow_et = (datetime.now(tz).date() + timedelta(days=0))
    # Format like 26FEB11
    suffix = tomorrow_et.strftime("%y%b%d").upper()

    events = []
    cursor = None
    while True:
        params = {
            "series_ticker": series_ticker,
            "status": "open",
            "limit": 200,
        }
        if cursor:
            params["cursor"] = cursor

        r = requests.get(
            KALSHI_BASE + KALSHI_EVENTS_PATH,
            params=params,
            headers={"User-Agent": KALSHI_USER_AGENT, "Accept": "application/json"},
            timeout=20,
        )
        r.raise_for_status()
        data = r.json()

        events.extend(data.get("events", []))
        cursor = data.get("cursor")
        if not cursor:
            break

    # Find the event with tomorrow's suffix in its ticker
    for e in events:
        et = e.get("event_ticker")
        if et and et.endswith(suffix):
            return et

    raise RuntimeError(f"Tomorrow event not found yet (looking for *{suffix}). Try again after market opens (~10am ET).")


# ======================
# KALSHI: MARKETS FOR EVENT
# ======================
def kalshi_get_markets_for_event(event_ticker: str):
    markets = []
    cursor = None

    while True:
        params = {"event_ticker": event_ticker, "limit": 1000, "mve_filter": "exclude"}
        if cursor:
            params["cursor"] = cursor

        r = requests.get(
            KALSHI_BASE + KALSHI_MARKETS_PATH,
            params=params,
            headers={"User-Agent": KALSHI_USER_AGENT, "Accept": "application/json"},
            timeout=20,
        )
        r.raise_for_status()
        data = r.json()

        markets.extend(data.get("markets", []))
        cursor = data.get("cursor")
        if not cursor:
            break

    return markets


def is_range_market(m: dict) -> bool:
    return (
        m.get("market_type") == "binary"
        and m.get("strike_type") == "between"
        and m.get("floor_strike") is not None
        and m.get("cap_strike") is not None
    )


def buy_price_prob_from_market(m: dict):
    yes_ask = m.get("yes_ask")
    yes_bid = m.get("yes_bid")
    last_price = m.get("last_price")

    if isinstance(yes_ask, int):
        buy = yes_ask / 100.0
    elif isinstance(last_price, int):
        buy = last_price / 100.0
    elif isinstance(yes_bid, int):
        buy = yes_bid / 100.0
    else:
        buy = None

    spread = None
    if isinstance(yes_ask, int) and isinstance(yes_bid, int):
        spread = (yes_ask - yes_bid) / 100.0

    return buy, spread


# ======================
# PROB MODEL
# ======================
def norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def prob_between_inclusive(low: float, high: float, mean: float, sigma: float) -> float:
    lo = (low - 0.5 - mean) / sigma
    hi = (high + 0.5 - mean) / sigma
    return max(0.0, min(1.0, norm_cdf(hi) - norm_cdf(lo)))


# ======================
# PICK BEST RANGE
# ======================
def pick_best_market(markets: list[dict], mean_high: float):
    best = None

    for m in markets:
        if not is_range_market(m):
            continue

        low = float(m["floor_strike"])
        high = float(m["cap_strike"])

        p_range = prob_between_inclusive(low, high, mean_high, SIGMA_F)
        buy_prob, spread_prob = buy_price_prob_from_market(m)

        if buy_prob is None:
            continue

        edge = p_range - buy_prob
        liquidity = m.get("liquidity") or 0

        if edge < MIN_EDGE:
            continue
        if spread_prob is not None and spread_prob > MAX_SPREAD:
            continue
        if liquidity < MIN_LIQUIDITY:
            continue

        cand = {
            "ticker": m.get("ticker"),
            "low": low,
            "high": high,
            "p_range": p_range,
            "buy_prob": buy_prob,
            "edge": edge,
            "spread": spread_prob,
            "liquidity": liquidity,
        }

        if best is None or cand["edge"] > best["edge"]:
            best = cand

    return best


# ======================
# MAIN
# ======================
def main():
    last_posted_pick = None
    last_event_ticker = None

    send_discord(
        f"📌 RANGE PICKER ONLINE (AUTO EVENT)\n"
        f"Series: **{SERIES_TICKER}**\n"
        f"Rule: edge ≥ {int(MIN_EDGE*100)}% | spread ≤ {int(MAX_SPREAD*100)}¢ | liquidity ≥ {MIN_LIQUIDITY}\n"
        f"Sigma: {SIGMA_F:.1f}°F\n"
        f"Time: {now_utc_str()}"
    )

    while True:
        try:
            event_ticker = kalshi_get_tomorrow_event_ticker(SERIES_TICKER)

            if event_ticker != last_event_ticker:
                last_event_ticker = event_ticker
                last_posted_pick = None
                send_discord(
                    f"🗓️ EVENT UPDATE\n"
                    f"Now tracking event: **{event_ticker}**\n"
                    f"Time: {now_utc_str()}"
                )

            period_name, mean_high, short_fc = get_tomorrow_high_mean()
            markets = kalshi_get_markets_for_event(event_ticker)
            best = pick_best_market(markets, mean_high)

            if best is None:
                if last_posted_pick != "NO_TRADE":
                    send_discord(
                        f"⚠️ NO TRADE — nothing clears filters\n"
                        f"Event: **{event_ticker}**\n"
                        f"NWS mean (tomorrow): **{mean_high}°F** ({period_name})\n"
                        f"Forecast: {short_fc}\n"
                        f"Time: {now_utc_str()}"
                    )
                    last_posted_pick = "NO_TRADE"
            else:
                if best["ticker"] != last_posted_pick:
                    spread_txt = "n/a" if best["spread"] is None else f"{best['spread']*100:.0f}¢"
                    send_discord(
                        f"🟢 BEST RANGE (Balanced)\n"
                        f"Event: **{event_ticker}**\n"
                        f"Pick: **{best['low']:.0f}–{best['high']:.0f}°F**\n"
                        f"Market: {best['ticker']}\n"
                        f"Our P(range): **{best['p_range']*100:.1f}%**\n"
                        f"Buy price: **{best['buy_prob']*100:.1f}%**\n"
                        f"Edge: **+{best['edge']*100:.1f}%** | Spread: {spread_txt} | Liquidity: {best['liquidity']}\n"
                        f"NWS mean (tomorrow): **{mean_high}°F** ({period_name})\n"
                        f"Forecast: {short_fc}\n"
                        f"Time: {now_utc_str()}"
                    )
                    last_posted_pick = best["ticker"]
                else:
                    print(f"[{now_utc_str()}] Best unchanged: {best['ticker']} edge={best['edge']*100:.1f}%")

        except Exception as e:
            print(f"[{now_utc_str()}] Error: {e}")

        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
