from __future__ import annotations

import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import feedparser
import httpx

from digest.config import get_settings
from digest.db import feed_cache_get, feed_cache_set
from digest.logging_util import setup_logging
from digest.textutil import clean_text

log = setup_logging()
TZ = ZoneInfo("Asia/Kolkata")
USER_AGENT = "DBotSportsDigest/2.0 (+discord)"

# Worldwide sources. Section labels come from article content, not feed name.
FEEDS: list[tuple[str, str]] = [
    # Global tops
    ("Sport", "https://feeds.bbci.co.uk/sport/rss.xml"),
    ("Sport", "https://www.espn.com/espn/rss/news"),
    ("Sport", "https://www.theguardian.com/sport/rss"),
    ("Sport", "https://www.thehindu.com/sport/feeder/default.rss"),
    ("Sport", "https://www.thehindu.com/sport/other-sports/feeder/default.rss"),
    # Football worldwide
    ("Football", "https://feeds.bbci.co.uk/sport/football/rss.xml"),
    ("Football", "https://www.espn.com/espn/rss/soccer/news"),
    ("Football", "https://www.thehindu.com/sport/football/feeder/default.rss"),
    # Cricket
    ("Cricket", "https://www.espncricinfo.com/rss/content/story/feeds/0.xml"),
    ("Cricket", "https://www.cricbuzz.com/rss-feeds/news"),
    ("Cricket", "https://feeds.bbci.co.uk/sport/cricket/rss.xml"),
    ("Cricket", "https://www.thehindu.com/sport/cricket/feeder/default.rss"),
    # Other major sports
    ("Tennis", "https://www.espn.com/espn/rss/tennis/news"),
    ("Tennis", "https://feeds.bbci.co.uk/sport/tennis/rss.xml"),
    ("NBA", "https://www.espn.com/espn/rss/nba/news"),
    ("NFL", "https://www.espn.com/espn/rss/nfl/news"),
    ("F1", "https://feeds.bbci.co.uk/sport/formula1/rss.xml"),
    ("Golf", "https://feeds.bbci.co.uk/sport/golf/rss.xml"),
    ("Rugby", "https://feeds.bbci.co.uk/sport/rugby-union/rss.xml"),
    ("Athletics", "https://feeds.bbci.co.uk/sport/athletics/rss.xml"),
]

# Display fallback order only. Live sort prefers Asian Games, then relevance.
SPORT_ORDER = [
    "World Highlights",
    "Asian Games",
    "Olympics",
    "Football",
    "Cricket",
    "Tennis",
    "NBA",
    "NFL",
    "F1",
    "Golf",
    "Rugby",
    "Badminton",
    "Hockey",
    "Athletics",
    "Boxing",
    "Wrestling",
    "Kabaddi",
    "MLB",
    "MMA",
    "Cycling",
    "Other",
]

SPORT_COLORS = {
    "World Highlights": 0x0D47A1,
    "Asian Games": 0xF4511E,
    "Olympics": 0x1565C0,
    "India Today": 0xFF9933,
    "Medal Tally": 0xC9A227,
    "Cricket": 0x1B8737,
    "Football": 0x1E88E5,
    "Tennis": 0xF9A825,
    "Badminton": 0x00897B,
    "Hockey": 0x5E35B1,
    "Athletics": 0x00838F,
    "Boxing": 0xC62828,
    "Wrestling": 0x6A1B9A,
    "Kabaddi": 0xEF6C00,
    "NBA": 0xE53935,
    "NFL": 0x6D4C41,
    "F1": 0xD32F2F,
    "Golf": 0x2E7D32,
    "Rugby": 0x6A1B9A,
    "MLB": 0xAD1457,
    "MMA": 0x424242,
    "Cycling": 0x00838F,
    "Other": 0x546E7A,
    "Sport": 0x546E7A,
}

SPORT_EMOJI = {
    "World Highlights": "\U0001f30d",
    "Asian Games": "\U0001f3c5",
    "Olympics": "\U0001f947",
    "India Today": "\U0001f1ee\U0001f1f3",
    "Medal Tally": "\U0001f3c6",
    "Cricket": "\U0001f3cf",
    "Football": "\u26bd",
    "Tennis": "\U0001f3be",
    "Badminton": "\U0001f3f8",
    "Hockey": "\U0001f3d1",
    "Athletics": "\U0001f3c3",
    "Boxing": "\U0001f94a",
    "Wrestling": "\U0001f93c",
    "Kabaddi": "\U0001f3c5",
    "NBA": "\U0001f3c0",
    "NFL": "\U0001f3c8",
    "F1": "\U0001f3ce",
    "Golf": "\u26f3",
    "Rugby": "\U0001f3c9",
    "MLB": "\u26be",
    "MMA": "\U0001f94a",
    "Cycling": "\U0001f6b4",
    "Other": "\U0001f4f0",
    "Sport": "\U0001f3c6",
}


@dataclass
class Article:
    title: str
    source: str
    publish_date: datetime
    article_text: str
    sport: str
    url: str
    india_relevant: bool | None = None
    competition: str | None = None
    event_match: bool = False
    meta: dict = field(default_factory=dict)


def entry_when(entry) -> datetime | None:
    """Use feed publish timestamp only, never body text dates."""
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    if not parsed:
        return None
    return datetime(*parsed[:6], tzinfo=timezone.utc).astimezone(TZ)


def source_from_url(url: str) -> str:
    return urlparse(url).netloc.lower().removeprefix("www.") or "unknown"


def normalize_article_url(url: str) -> str:
    return (
        url.replace("://www.cricinfo.com/", "://www.espncricinfo.com/")
        .replace("://cricinfo.com/", "://www.espncricinfo.com/")
        .split("?")[0]
        .rstrip("/")
    )


def fetch_with_retries(client: httpx.Client, url: str, attempts: int = 2) -> bytes | None:
    last_exc: Exception | None = None
    for i in range(attempts):
        try:
            resp = client.get(url, follow_redirects=True, timeout=8.0)
            resp.raise_for_status()
            return resp.content
        except Exception as exc:
            last_exc = exc
            if i + 1 < attempts:
                time.sleep(0.35 * (i + 1))
    log.warning("feed_failed url=%s reason=%s", url, type(last_exc).__name__ if last_exc else "unknown")
    return None


def fetch_page_text(url: str, client: httpx.Client, limit: int = 1800) -> str:
    if "/videos/" in url or "/av/" in url:
        return ""
    content = fetch_with_retries(client, url, attempts=1)
    if not content:
        return ""
    html = content.decode("utf-8", errors="ignore")
    html = re.sub(r"(?is)<(script|style|nav|footer|header).*?>.*?</\1>", " ", html)
    paras = [clean_text(p) for p in re.findall(r"(?is)<p[^>]*>(.*?)</p>", html)]
    paras = [p for p in paras if len(p.split()) >= 8]
    if paras:
        return " ".join(paras)[:limit]
    meta = re.search(
        r'(?is)<meta[^>]+(?:name|property)=["\'](?:description|og:description)["\'][^>]+content=["\']([^"\']+)',
        html,
    )
    return clean_text(meta.group(1))[:limit] if meta else ""


_ASIAN_GAMES_RE = re.compile(
    r"\b("
    r"asian games|aichi[- ]?nagoya|nagoya 2026|asian-games|"
    r"asian games 2026|ag2026"
    r")\b",
    re.I,
)

_EVENT_RULES: list[tuple[re.Pattern[str], str]] = [
    (_ASIAN_GAMES_RE, "Asian Games"),
    (re.compile(r"\bparalympics?\b", re.I), "Paralympics"),
    (re.compile(r"\bcommonwealth games\b", re.I), "Commonwealth Games"),
    # Olympics only when not an Asian Games story (checked first above).
    (re.compile(r"\bolympics?\b|\bolympic games\b", re.I), "Olympics"),
]

_SPORT_RULES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\b(cricket|odi|t20|test match|wicket|ipl|bcci)\b", re.I), "Cricket"),
    (re.compile(r"\b(football|soccer|premier league|la liga|serie a|bundesliga|champions league|fifa|mls)\b", re.I), "Football"),
    (re.compile(r"\b(tennis|wimbledon|us open|french open|australian open|atp|wta|china open)\b", re.I), "Tennis"),
    (re.compile(r"\bbadminton\b", re.I), "Badminton"),
    (re.compile(r"\b(field hockey|ice hockey|nhl|hockey)\b", re.I), "Hockey"),
    (re.compile(r"\b(athletics|marathon|sprint|javelin|shot put)\b", re.I), "Athletics"),
    (re.compile(r"\bboxing\b", re.I), "Boxing"),
    (re.compile(r"\bwrestling\b", re.I), "Wrestling"),
    (re.compile(r"\bkabaddi\b", re.I), "Kabaddi"),
    (re.compile(r"\bnba\b|basketball", re.I), "NBA"),
    (re.compile(r"\bnfl\b|american football", re.I), "NFL"),
    (re.compile(r"\bmlb\b|baseball|world series", re.I), "MLB"),
    (re.compile(r"\bformula\s*1\b|\bf1\b|grand prix\b", re.I), "F1"),
    (re.compile(r"\bgolf\b|pga\b|masters tournament", re.I), "Golf"),
    (re.compile(r"\brugby\b", re.I), "Rugby"),
    (re.compile(r"\b(ufc|mma|mixed martial)\b", re.I), "MMA"),
    (re.compile(r"\b(cycling|tour de france|giro)\b", re.I), "Cycling"),
]


def classify_sport(default_sport: str, title: str, url: str, summary: str = "") -> str:
    """
    Pick a section from yesterday's article text.
    Asian Games always wins over Olympics/Other when mentioned.
    """
    blob = f"{title} {url} {summary}"
    if _ASIAN_GAMES_RE.search(blob) or "asian-games" in url.lower():
        return "Asian Games"
    for pattern, label in _EVENT_RULES:
        if pattern.search(blob):
            return label
    for pattern, label in _SPORT_RULES:
        if pattern.search(blob):
            return label
    if default_sport in {"Sport", "Other", ""}:
        return "Other"
    return default_sport


def reclassify_article(article: Article) -> Article:
    """Re-run classifier after body enrich so Asian Games can't land in Other."""
    resolved = classify_sport(article.sport, article.title, article.url, article.article_text)
    if resolved != article.sport:
        log.info("reclassify %s -> %s title=%s", article.sport, resolved, article.title[:90])
        article.sport = resolved
    return article


def _parse_feed(sport: str, feed_url: str, raw: bytes) -> list[Article]:
    articles: list[Article] = []
    try:
        feed = feedparser.parse(raw)
    except Exception:
        return articles
    for entry in feed.entries:
        link = normalize_article_url((entry.get("link") or "").strip())
        title = clean_text(entry.get("title") or "")
        when = entry_when(entry)
        summary = clean_text(entry.get("summary") or entry.get("description") or "")
        if not link or not title or not when:
            continue
        resolved = classify_sport(sport, title, link, summary)
        articles.append(
            Article(
                title=title,
                source=source_from_url(link),
                publish_date=when,
                article_text=summary,
                sport=resolved,
                url=link,
            )
        )
    return articles


def _fetch_one_feed(
    sport: str, feed_url: str, *, bypass_cache: bool = False
) -> tuple[str, str, list[Article] | None]:
    minutes = get_settings().feed_cache_minutes
    if not bypass_cache:
        cached = feed_cache_get(feed_url, max_age_minutes=minutes)
        if cached is not None:
            log.debug("feed_cache_hit url=%s", feed_url)
            return sport, feed_url, _parse_feed(sport, feed_url, cached)

    headers = {"User-Agent": USER_AGENT}
    with httpx.Client(headers=headers) as client:
        raw = fetch_with_retries(client, feed_url)
    if raw is None:
        return sport, feed_url, None
    feed_cache_set(feed_url, raw)
    return sport, feed_url, _parse_feed(sport, feed_url, raw)


def fetch_feed_articles(*, bypass_cache: bool = False) -> tuple[list[Article], list[str]]:
    articles: list[Article] = []
    failures: list[str] = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [
            pool.submit(_fetch_one_feed, sport, url, bypass_cache=bypass_cache)
            for sport, url in FEEDS
        ]
        for fut in as_completed(futures):
            sport, feed_url, items = fut.result()
            if items is None:
                failures.append(feed_url)
                continue
            articles.extend(items)
            log.info("feed_ok sport=%s url=%s kept=%s", sport, feed_url, len(items))
    return articles, failures


def _enrich_one(article: Article) -> Article:
    body = article.article_text
    # Skip page fetch when RSS summary already has enough substance.
    if len(body.split()) >= 55:
        return reclassify_article(article)
    # Skip enrich for already-cached article page payloads.
    page_key = f"page:{article.url}"
    from digest.db import cache_get, cache_set

    cached_page = cache_get(page_key, max_age_minutes=180)
    if cached_page and cached_page.get("text"):
        article.article_text = clean_text(f"{body} {cached_page['text']}")
        return reclassify_article(article)

    headers = {"User-Agent": USER_AGENT}
    with httpx.Client(headers=headers, timeout=8.0) as client:
        page = fetch_page_text(article.url, client)
        if page:
            cache_set(page_key, {"text": page})
            body = clean_text(f"{body} {page}")
    article.article_text = body
    return reclassify_article(article)


def enrich_article_text(articles: list[Article]) -> list[Article]:
    if not articles:
        return []
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = [pool.submit(_enrich_one, a) for a in articles]
        # Preserve prefilter priority order (Asian Games / relevance).
        return [fut.result() for fut in futures]
