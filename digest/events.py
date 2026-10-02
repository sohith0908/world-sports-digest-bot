from __future__ import annotations

import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import httpx

from digest.logging_util import setup_logging

log = setup_logging()
TZ = ZoneInfo("Asia/Kolkata")


def yesterday_ist() -> str:
    return (datetime.now(TZ) - timedelta(days=1)).strftime("%Y-%m-%d")


def fetch_football_results() -> list[dict]:
    """Event-first football results via football-data.org (optional API key)."""
    key = os.getenv("FOOTBALL_DATA_API_KEY", "").strip()
    if not key:
        return []
    day = yesterday_ist()
    url = f"https://api.football-data.org/v4/matches?dateFrom={day}&dateTo={day}&status=FINISHED"
    try:
        with httpx.Client(timeout=15.0) as client:
            resp = client.get(url, headers={"X-Auth-Token": key})
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        log.warning("football_data_failed reason=%s", type(exc).__name__)
        return []

    events = []
    for match in data.get("matches", []):
        home = (match.get("homeTeam") or {}).get("name") or ""
        away = (match.get("awayTeam") or {}).get("name") or ""
        score = match.get("score", {}).get("fullTime", {})
        events.append(
            {
                "sport": "Football",
                "home": home,
                "away": away,
                "score": f"{score.get('home', '?')}-{score.get('away', '?')}",
                "competition": ((match.get("competition") or {}).get("name") or ""),
            }
        )
    log.info("football_events_fetched count=%s day=%s", len(events), day)
    return events


def fetch_thesportsdb_events() -> list[dict]:
    """Optional TheSportsDB events for yesterday (free demo key works lightly)."""
    key = os.getenv("THESPORTSDB_API_KEY", "123").strip() or "123"
    day = yesterday_ist()
    url = f"https://www.thesportsdb.com/api/v1/json/{key}/eventsday.php?d={day}"
    try:
        with httpx.Client(timeout=15.0) as client:
            resp = client.get(url)
            resp.raise_for_status()
            data = resp.json()
    except Exception:
        return []
    events = []
    for item in data.get("events") or []:
        events.append(
            {
                "sport": item.get("strSport") or "",
                "home": item.get("strHomeTeam") or "",
                "away": item.get("strAwayTeam") or "",
                "score": f"{item.get('intHomeScore', '?')}-{item.get('intAwayScore', '?')}",
                "competition": item.get("strLeague") or "",
            }
        )
    log.info("thesportsdb_events count=%s", len(events))
    return events


def yesterday_events() -> list[dict]:
    events = []
    events.extend(fetch_football_results())
    # Keep TheSportsDB as a soft supplement; demo key is rate-limited.
    if not events:
        events.extend(fetch_thesportsdb_events())
    return events


def article_matches_events(title: str, body: str, events: list[dict]) -> bool:
    blob = f"{title} {body[:300]}".lower()
    for event in events:
        home = (event.get("home") or "").lower()
        away = (event.get("away") or "").lower()
        comp = (event.get("competition") or "").lower()
        if home and home in blob:
            return True
        if away and away in blob:
            return True
        if comp and len(comp) > 4 and comp in blob:
            return True
    return False
