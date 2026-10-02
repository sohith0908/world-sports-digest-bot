"""Regression checks for worldwide sports highlights digest."""
from __future__ import annotations

import sys
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

from digest.feeds import Article, TZ, classify_sport, fetch_feed_articles, reclassify_article
from digest.filters import FINAL_CAPS, TOTAL_FINAL_MAX, finalize_by_relevance, prefilter, same_event, within_news_window
from digest.format_embeds import build_embeds
from digest.quality import is_non_story, title_is_opinion
from digest.textutil import extract_scoreline
from digest.world_roundup import build_world_highlights_text, sports_covered


def check(name: str, ok: bool, detail: str = "") -> bool:
    status = "PASS" if ok else "FAIL"
    extra = f" — {detail}" if detail else ""
    print(f"[{status}] {name}{extra}")
    return ok


def unit_checks() -> list[str]:
    fails: list[str] = []
    if not check(
        "reject CTE evergreen",
        title_is_opinion("Today’s NFL players grew up knowing about CTE: Here’s why they risk it"),
    ):
        fails.append("cte")
    if not check(
        "reject guide/storylines",
        title_is_opinion("Our 30-team guide to NBA training camps: Biggest storylines")
        or is_non_story("Our 30-team guide to NBA training camps: Biggest storylines"),
    ):
        fails.append("guide")
    if not check(
        "asian games not other",
        classify_sport("Other", "Chikitha archery gold", "https://x.com/a", "Asian Games debut")
        == "Asian Games",
    ):
        fails.append("ag_classify")
    body_ag = Article(
        title="Karate black belt to archery gold",
        source="hindu",
        publish_date=datetime.now(TZ),
        article_text="She won two Asian Games gold medals on debut in Nagoya.",
        sport="Other",
        url="https://example.com/chikitha",
    )
    reclassify_article(body_ag)
    if not check("reclassify asian games from body", body_ag.sport == "Asian Games", body_ag.sport):
        fails.append("ag_reclass")
    a = Article(
        "Despite medical timeout, Djokovic starts China Open with win",
        "espn",
        datetime.now(TZ),
        "x",
        "Tennis",
        "u1",
    )
    b = Article(
        "Djokovic wins first match since Wimbledon at China Open",
        "bbc",
        datetime.now(TZ),
        "x",
        "Tennis",
        "u2",
    )
    if not check("event dedupe djokovic", same_event(a, b)):
        fails.append("dedupe")
    if not check("nba/nfl final caps are 1", FINAL_CAPS.get("NBA") == 1 and FINAL_CAPS.get("NFL") == 1):
        fails.append("caps")
    if not check("scoreline extract", extract_scoreline("Arsenal 2-1 Chelsea in London") == "Arsenal 2-1 Chelsea"):
        fails.append("scoreline")
    if not check("date window rejects 3-day-old", not within_news_window(datetime.now(TZ) - timedelta(days=3))):
        fails.append("date")
    return fails


def live_checks() -> list[str]:
    fails: list[str] = []
    from digest.ai import Summary

    articles, failures = fetch_feed_articles()
    distinct = {a.sport for a in articles if a.sport}
    if not check("feeds returned articles", len(articles) > 30, f"n={len(articles)} fails={len(failures)}"):
        fails.append("fetch")
    if not check("multi-sport raw feed", len(distinct) >= 5, str(sorted(distinct)[:12])):
        fails.append("breadth")

    skip_reasons: dict[str, int] = {}
    candidates = prefilter(articles, skip_reasons)
    cand_sports = Counter(a.sport for a in candidates)
    if not check("prefilter multi-sport", len(cand_sports) >= 4, str(dict(cand_sports))):
        fails.append("prefilter")

    pairs = [
        (
            a,
            Summary(
                headline=a.title[:120],
                key_fact=(a.article_text or a.title)[:180],
                why_it_matters=None,
                url=a.url,
                sport=a.sport,
                india_relevant=bool(a.india_relevant),
                competition=a.competition,
            ),
        )
        for a in candidates
    ]
    finals = finalize_by_relevance(pairs)
    sports = sports_covered(finals)
    if not check("finalize several sports", len(sports) >= 4, str(sports)):
        fails.append("finalize")
    nba_n = sum(1 for s in finals if s.sport == "NBA")
    nfl_n = sum(1 for s in finals if s.sport == "NFL")
    if not check("nba/nfl caps", nba_n <= 1 and nfl_n <= 1, f"nba={nba_n} nfl={nfl_n}"):
        fails.append("final_caps")

    text = build_world_highlights_text(finals)
    if not check("world highlights bullets", text.count("•") >= 3, f"n={text.count('•')}"):
        fails.append("world")

    header, main, details = build_embeds(finals, thread_details=True)
    if not check("world embed first", main and "WORLD HIGHLIGHTS" in (main[0].title or "").upper()):
        fails.append("embed")
    if not check("header world", "world" in header.lower()):
        fails.append("header")
    if not check("thread details present", len(details) >= 1, str(len(details))):
        fails.append("details")
    if not check("embed caps", len(main) <= 10 and len(details) <= 10):
        fails.append("embed_cap")

    print("\n--- World Highlights preview ---")
    print(text[:1200])
    return fails


def main() -> int:
    print("======== ROUND 1 ========")
    fails = unit_checks() + live_checks()
    if fails:
        print("Failed:", fails)
        return 1
    print("\nALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
