from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import httpx

from digest.config import get_settings
from digest.logging_util import setup_logging

log = setup_logging()
TZ = ZoneInfo("Asia/Kolkata")


def yesterday_ist() -> str:
    return (datetime.now(TZ) - timedelta(days=1)).strftime("%Y-%m-%d")


def fetch_football_results() -> list[dict]:
    """Event-first football results via football-data.org (optional API key)."""
    key = get_settings().football_data_api_key
    if not key:
        return []
    day = yesterday_ist()
    url = f"https://api.football-data.org/v4/matches?dateFrom={day}&dateTo={day}&status=FINISHED"
    try:
        with httpx.Client(timeout=12.0) as client:
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
    """TheSportsDB events for yesterday — always attempted as a global supplement."""
    key = get_settings().thesportsdb_api_key
    day = yesterday_ist()
    url = f"https://www.thesportsdb.com/api/v1/json/{key}/eventsday.php?d={day}"
    try:
        with httpx.Client(timeout=12.0) as client:
            resp = client.get(url)
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        log.warning("thesportsdb_failed reason=%s", type(exc).__name__)
        return []
    events = []
    for item in data.get("events") or []:
        home = item.get("strHomeTeam") or ""
        away = item.get("strAwayTeam") or ""
        if not home and not away:
            continue
        events.append(
            {
                "sport": item.get("strSport") or "",
                "home": home,
                "away": away,
                "score": f"{item.get('intHomeScore', '?')}-{item.get('intAwayScore', '?')}",
                "competition": item.get("strLeague") or "",
            }
        )
    log.info("thesportsdb_events count=%s", len(events))
    return events


def yesterday_events() -> list[dict]:
    """Fetch football-data + TheSportsDB in parallel and merge."""
    with ThreadPoolExecutor(max_workers=2) as pool:
        fut_fb = pool.submit(fetch_football_results)
        fut_ts = pool.submit(fetch_thesportsdb_events)
        football = fut_fb.result()
        sportsdb = fut_ts.result()

    seen: set[tuple[str, str, str]] = set()
    merged: list[dict] = []
    for event in football + sportsdb:
        key = (
            (event.get("home") or "").lower(),
            (event.get("away") or "").lower(),
            (event.get("competition") or "").lower(),
        )
        if key in seen:
            continue
        seen.add(key)
        merged.append(event)
    log.info("yesterday_events_total count=%s", len(merged))
    return merged


def article_matches_events(title: str, body: str, events: list[dict]) -> bool:
    blob = f"{title} {body[:400]}".lower()
    for event in events:
        home = (event.get("home") or "").lower()
        away = (event.get("away") or "").lower()
        comp = (event.get("competition") or "").lower()
        score = (event.get("score") or "").lower()
        if home and len(home) > 3 and home in blob:
            return True
        if away and len(away) > 3 and away in blob:
            return True
        if home and away and home.split()[-1] in blob and away.split()[-1] in blob:
            return True
        if comp and len(comp) > 4 and comp in blob and score and score.replace("?", "") in blob:
            return True
        if comp and len(comp) > 4 and comp in blob:
            return True
    return False
