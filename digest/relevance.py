from __future__ import annotations

import re

from digest.feeds import Article
from digest.logging_util import setup_logging
from digest.registry import competition_score, stage_score

log = setup_logging()

# Balanced worldwide baselines — India boost is additive, not exclusive.
SPORT_BASE = {
    "Asian Games": 42,
    "Olympics": 38,
    "Football": 36,
    "Cricket": 34,
    "Tennis": 30,
    "F1": 28,
    "Golf": 26,
    "Rugby": 26,
    "NBA": 24,
    "NFL": 22,
    "MLB": 22,
    "Athletics": 28,
    "Badminton": 26,
    "Hockey": 26,
    "Boxing": 24,
    "Wrestling": 24,
    "Kabaddi": 24,
    "MMA": 22,
    "Cycling": 22,
    "Other": 18,
    "Sport": 18,
}

INDIA_RE = re.compile(
    r"\b("
    r"india|indian|bharat|bcci|team india|men in blue|"
    r"kohli|rohit|bumrah|gill|jadeja|hardik|pant|suryakumar|kuldeep|axar|"
    r"neeraj|sindhu|saina|manika|satwik|chirag|mirabai|lakshya|"
    r"lovlina|chikitha|taniparthi|patil|neeru|kynan|"
    r"isl\b|indian super league|asian games"
    r")\b",
    re.I,
)

HARD_NEWS = re.compile(
    r"\b(guilty|verdict|charged|banned|suspended|fined|signed?|signs|"
    r"ruled out|injury|trade[ds]?|won|beat|defeat|record sale|"
    r"gold|silver|bronze|medal|qualified|championship)\b",
    re.I,
)

SOFT_FEATURE = re.compile(
    r"\b(no different|in his own words|exclusive interview|kick-?off is at|"
    r"too shy for|meet [\w']+:|here'?s why|guide to|storylines?)\b",
    re.I,
)


def relevance_score(article: Article) -> tuple[int, dict[str, int]]:
    text = f"{article.title}\n{article.article_text[:900]}"
    parts: dict[str, int] = {}
    parts["sport"] = SPORT_BASE.get(article.sport, 18)

    india = bool(article.india_relevant) or bool(INDIA_RE.search(text))
    # Soft India boost so worldwide results still compete.
    parts["india"] = 28 if india else 0
    article.india_relevant = india

    comp_pts, comp_name = competition_score(text)
    parts["competition"] = min(comp_pts, 90)
    if comp_name:
        article.competition = comp_name
        if comp_name == "Asian Games" and article.sport != "Asian Games":
            article.sport = "Asian Games"
            parts["sport"] = SPORT_BASE["Asian Games"]

    parts["stage"] = stage_score(text)
    parts["hard_news"] = 35 if HARD_NEWS.search(text) else 0
    parts["soft_feature"] = -45 if SOFT_FEATURE.search(text) else 0
    parts["event_match"] = 20 if article.event_match else 0

    try:
        parts["recency"] = int(article.publish_date.timestamp() % 10_000) // 1000
    except Exception:
        parts["recency"] = 0

    return sum(parts.values()), parts


def log_relevance(article: Article, score: int, parts: dict[str, int]) -> None:
    log.info(
        "relevance sport=%s score=%s india=%s competition=%s parts=%s title=%s",
        article.sport,
        score,
        "yes" if parts.get("india") else "no",
        article.competition or "-",
        ",".join(f"{k}:{v}" for k, v in parts.items()),
        article.title[:90],
    )
