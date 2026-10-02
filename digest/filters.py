from __future__ import annotations

import re
from datetime import datetime, timedelta
from difflib import SequenceMatcher
from urllib.parse import urlparse

from digest.db import is_posted, log_skip
from digest.feeds import Article, TZ
from digest.logging_util import setup_logging
from digest.quality import is_non_story, title_is_opinion
from digest.registry import detect_unregistered_events
from digest.relevance import log_relevance, relevance_score, source_trust_score
from digest.textutil import clean_text

log = setup_logging()

REJECT_PATH = re.compile(r"/(blog|opinion|fantasy|betting|column)(/|$)", re.I)

# Soft region buckets for worldwide balance in the final digest.
REGION_SPORTS = {
    "asia": {
        "Asian Games",
        "Cricket",
        "Badminton",
        "Hockey",
        "Kabaddi",
        "Wrestling",
        "Boxing",
        "Athletics",
    },
    "europe": {"Football", "Tennis", "F1", "Golf", "Rugby", "Olympics"},
    "americas": {"NBA", "NFL", "MLB", "MMA"},
}

# Candidates per sport before AI — quality/breadth first (speed later).
CANDIDATE_CAPS: dict[str, int] = {
    "Asian Games": 4,
    "Football": 3,
    "Cricket": 3,
    "Tennis": 2,
    "F1": 2,
    "Golf": 2,
    "Rugby": 2,
    "NBA": 2,
    "NFL": 2,
    "Olympics": 2,
    "Athletics": 2,
    "Other": 2,
}
MAX_AI_CANDIDATES = 24
DEFAULT_CANDIDATE_CAP = 2
FINAL_CAPS: dict[str, int] = {
    "Asian Games": 3,
    "Football": 2,
    "Cricket": 2,
    "Tennis": 1,
    "F1": 1,
    "Golf": 1,
    "Rugby": 1,
    "NBA": 1,
    "NFL": 1,
    "Olympics": 1,
    "Athletics": 1,
    "Boxing": 1,
    "Wrestling": 1,
    "Badminton": 1,
    "Hockey": 1,
    "MLB": 1,
    "MMA": 1,
    "Cycling": 1,
    "Snooker": 1,
    "Other": 1,
}
DEFAULT_FINAL_CAP = 1
TOTAL_FINAL_MIN = 8
TOTAL_FINAL_MAX = 14

_STOP = {
    "the", "a", "an", "to", "for", "with", "after", "as", "in", "on", "at", "of",
    "and", "or", "vs", "v", "by", "from", "is", "are", "was", "were", "wins", "win",
    "over", "into", "his", "her", "their", "how", "what", "why", "meet", "latest",
    "says", "said", "will", "not", "only", "first", "second", "third", "team",
    "match", "game", "open", "despite", "starts", "start", "since", "about",
}


def within_news_window(when: datetime, now: datetime | None = None) -> bool:
    """Yesterday 00:00 IST through now — evergreen republishes still need keyword filters."""
    now = now or datetime.now(TZ)
    start = (now - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return start <= when <= now + timedelta(hours=2)


def title_too_similar(a: str, b: str, threshold: float = 0.8) -> bool:
    return SequenceMatcher(None, a.lower(), b.lower()).ratio() >= threshold


def significant_tokens(title: str) -> set[str]:
    words = re.findall(r"[a-z0-9\*]+", title.lower())
    return {w for w in words if len(w) > 2 and w not in _STOP}


def event_overlap(a: str, b: str) -> float:
    sa, sb = significant_tokens(a), significant_tokens(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def same_event(a: Article, b: Article) -> bool:
    """Collapse multi-source duplicates (Djokovic China Open, Taniparthi profiles)."""
    if event_overlap(a.title, b.title) >= 0.45:
        return True
    # Shared rare surname/token of length >= 6 plus a shared competition/number cue.
    sa, sb = significant_tokens(a.title), significant_tokens(b.title)
    rare = {t for t in sa & sb if len(t) >= 6}
    if not rare:
        return False
    nums_a = set(re.findall(r"\b\d{2,3}\*?\b", a.title.lower()))
    nums_b = set(re.findall(r"\b\d{2,3}\*?\b", b.title.lower()))
    if nums_a and nums_a & nums_b:
        return True
    events = ("china", "asian", "odi", "test", "wimbledon", "archery", "wrestling", "squash")
    blob_a = a.title.lower()
    blob_b = b.title.lower()
    if any(e in blob_a and e in blob_b for e in events):
        return True
    # Same athlete full name fragment across soft profiles.
    if len(rare) >= 1 and (a.india_relevant or b.india_relevant) and a.sport == b.sport:
        return event_overlap(a.title, b.title) >= 0.28
    return False


def dedupe_events(scored: list[tuple[int, Article, dict]]) -> list[tuple[int, Article, dict]]:
    """Keep the highest-scoring copy; break ties with source trust (BBC/cricinfo > soft ESPN)."""
    kept: list[tuple[int, Article, dict]] = []
    for score, article, parts in scored:
        replaced = False
        for i, (prev_score, prev, prev_parts) in enumerate(kept):
            if not same_event(article, prev):
                continue
            trust_new = source_trust_score(article.source or article.url)
            trust_old = source_trust_score(prev.source or prev.url)
            if score > prev_score or (score == prev_score and trust_new > trust_old):
                log.info(
                    "event_dup_prefer title=%s over=%s trust=%s>%s",
                    article.title[:70],
                    prev.title[:70],
                    trust_new,
                    trust_old,
                )
                kept[i] = (score, article, parts)
            else:
                log.info(
                    "skip event_dup title=%s kept=%s",
                    article.title[:80],
                    prev.title[:80],
                )
            replaced = True
            break
        if not replaced:
            kept.append((score, article, parts))
    return kept


def prefilter(articles: list[Article], skip_reasons: dict[str, int]) -> list[Article]:
    kept: list[Article] = []
    seen_urls: set[str] = set()
    kept_titles: list[str] = []

    for article in articles:
        url = article.url
        path = urlparse(url).path.lower()
        title = clean_text(article.title)

        def bump(reason: str):
            skip_reasons[reason] = skip_reasons.get(reason, 0) + 1
            log_skip(url, title, reason)
            log.info("skip %s title=%s", reason, title[:100])

        if is_posted(url) or url in seen_urls:
            bump("dup_url")
            continue
        if not within_news_window(article.publish_date):
            bump("not_last_24h")
            continue
        if title_is_opinion(title):
            bump("opinion")
            continue
        if is_non_story(title, article.article_text):
            bump("non_story")
            continue
        if REJECT_PATH.search(path) or "/videos/" in path or "/av/" in path:
            bump("bad_path")
            continue
        if any(title_too_similar(title, prev) for prev in kept_titles):
            bump("near_dup_title")
            continue

        seen_urls.add(url)
        kept_titles.append(title)
        article.title = title
        article.article_text = clean_text(article.article_text)
        kept.append(article)

    emerging = detect_unregistered_events([a.title for a in kept])
    if emerging:
        log.info("emerging_events %s", emerging)

    scored = []
    for article in kept:
        score, parts = relevance_score(article)
        log_relevance(article, score, parts)
        scored.append((score, article, parts))

    # Highest relevance first so event dedupe keeps the best copy.
    scored.sort(key=lambda row: -row[0])
    scored = dedupe_events(scored)

    by_sport: dict[str, list[tuple[int, Article]]] = {}
    for score, article, _ in scored:
        bucket = by_sport.setdefault(article.sport, [])
        cap = CANDIDATE_CAPS.get(article.sport, DEFAULT_CANDIDATE_CAP)
        if len(bucket) < cap:
            bucket.append((score, article))
            log.info(
                "candidate_selected sport=%s score=%s title=%s",
                article.sport,
                score,
                article.title[:90],
            )

    # Round-robin: one from each sport first (worldwide coverage), then extras.
    sport_rank: list[tuple[int, int, str]] = []
    for sport, items in by_sport.items():
        best = max((s for s, _ in items), default=0)
        priority = 0 if sport == "Asian Games" else 1
        sport_rank.append((priority, -best, sport))
    sport_rank.sort()

    ordered: list[Article] = []
    # Pass A — first highlight per sport
    for _, __, sport in sport_rank:
        items = by_sport.get(sport) or []
        if items:
            ordered.append(items[0][1])
        if len(ordered) >= MAX_AI_CANDIDATES:
            return ordered
    # Pass B — remaining candidates for depth (still capped)
    for _, __, sport in sport_rank:
        items = by_sport.get(sport) or []
        for _, article in items[1:]:
            ordered.append(article)
            if len(ordered) >= MAX_AI_CANDIDATES:
                return ordered
    return ordered


def finalize_by_relevance(pairs: list[tuple[Article, object]]) -> list[object]:
    from digest.ai import Summary

    scored: list[tuple[int, Summary, Article]] = []
    for article, summary in pairs:
        if not isinstance(summary, Summary):
            continue
        # Never leave Asian Games stories under Other / Olympics / Sport.
        blob = f"{article.title} {article.article_text[:600]} {summary.headline} {summary.key_fact}"
        if re.search(r"\basian games\b|asian-games|aichi|nagoya 2026", blob, re.I):
            summary.sport = "Asian Games"
            article.sport = "Asian Games"
            summary.competition = summary.competition or "Asian Games"
        elif summary.competition == "Asian Games":
            summary.sport = "Asian Games"
            article.sport = "Asian Games"

        score, parts = relevance_score(article)
        if summary.india_relevant:
            score += 10
        log.info(
            "final_score sport=%s score=%s india=%s competition=%s headline=%s",
            summary.sport,
            score,
            "yes" if summary.india_relevant or parts.get("india") else "no",
            summary.competition or article.competition or "-",
            summary.headline[:90],
        )
        scored.append((score, summary, article))

    scored.sort(key=lambda row: (0 if row[1].sport == "Asian Games" else 1, -row[0]))

    # Event-level dedupe on finalized headlines too.
    unique: list[tuple[int, Summary, Article]] = []
    for score, summary, article in scored:
        dup = False
        for _, prev_s, prev_a in unique:
            fake_a = Article(
                title=summary.headline,
                source="",
                publish_date=article.publish_date,
                article_text=summary.key_fact,
                sport=summary.sport,
                url=summary.url,
                india_relevant=summary.india_relevant,
            )
            fake_b = Article(
                title=prev_s.headline,
                source="",
                publish_date=prev_a.publish_date,
                article_text=prev_s.key_fact,
                sport=prev_s.sport,
                url=prev_s.url,
                india_relevant=prev_s.india_relevant,
            )
            if same_event(fake_a, fake_b) or same_event(article, prev_a):
                log.info("skip final_event_dup headline=%s", summary.headline[:80])
                dup = True
                break
        if not dup:
            unique.append((score, summary, article))

    def region_of(sport: str) -> str:
        for name, sports in REGION_SPORTS.items():
            if sport in sports:
                return name
        return "other"

    # Pass 1a: one story per region that has candidates (US / Europe / Asia balance).
    counts: dict[str, int] = {}
    out: list[Summary] = []
    regions_hit: set[str] = set()
    for score, summary, _ in unique:
        region = region_of(summary.sport)
        if region in regions_hit or region == "other":
            continue
        if summary.sport in counts:
            continue
        regions_hit.add(region)
        counts[summary.sport] = 1
        out.append(summary)

    # Pass 1b: one highlight per remaining sport.
    for score, summary, _ in unique:
        if summary.sport in counts:
            continue
        if len(out) >= TOTAL_FINAL_MAX:
            break
        counts[summary.sport] = 1
        out.append(summary)

    # Pass 2: fill remaining slots by score within per-sport caps.
    for score, summary, _ in unique:
        if summary in out:
            continue
        used = counts.get(summary.sport, 0)
        cap = FINAL_CAPS.get(summary.sport, DEFAULT_FINAL_CAP)
        if used >= cap:
            continue
        if len(out) >= TOTAL_FINAL_MAX:
            break
        counts[summary.sport] = used + 1
        out.append(summary)

    # Keep Asian Games / mega-events near front, then original score order.
    rank = {id(s): i for i, s in enumerate(out)}

    def sort_key(s: Summary) -> tuple[int, int]:
        mega = 0 if s.sport in {"Asian Games", "Olympics"} else 1
        return (mega, rank.get(id(s), 99))

    out.sort(key=sort_key)
    log.info("finalize kept=%s sports=%s", len(out), dict(counts))
    return out
