from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Awaitable, Callable

import discord

from digest.ai import summarize_article
from digest.config import get_settings
from digest.db import add_pending_review, mark_posted_many, save_health_run
from digest.events import article_matches_events, yesterday_events
from digest.feeds import TZ, enrich_article_text, fetch_feed_articles, reclassify_article
from digest.filters import finalize_by_relevance, prefilter
from digest.format_embeds import build_embeds
from digest.logging_util import setup_logging
from digest.verify import ai_verify_extreme

log = setup_logging()

SendEmbeds = Callable[[str, list[discord.Embed], list[discord.Embed]], Awaitable[None] | None]
SendText = Callable[[str], Awaitable[None] | None]
SendReview = Callable[[dict], Awaitable[None] | None]
ProgressFn = Callable[[str], Awaitable[None] | None]


def _summarize_one(article, human_review: bool):
    summary, flags, reason = summarize_article(article)
    return article, summary, flags, reason, human_review


def build_digest(
    *,
    bypass_cache: bool = False,
    dry_run: bool = False,
    progress: Callable[[str], None] | None = None,
) -> dict:
    def note(msg: str) -> None:
        if progress:
            try:
                progress(msg)
            except Exception:
                pass

    stats = {
        "fetched": 0,
        "skipped": 0,
        "posted": 0,
        "pending": 0,
        "feed_failures": [],
        "skip_reasons": {},
        "header": "",
        "embeds": [],
        "detail_embeds": [],
        "auto_items": [],
        "review_items": [],
        "error": None,
        "dry_run": dry_run,
    }
    try:
        note("Fetching worldwide sports feeds…")
        articles, failures = fetch_feed_articles(bypass_cache=bypass_cache)
        stats["feed_failures"] = failures
        stats["fetched"] = len(articles)

        note("Matching yesterday’s results…")
        events = yesterday_events()
        for article in articles:
            if article_matches_events(article.title, article.article_text, events):
                article.event_match = True

        skip_reasons: dict[str, int] = {}
        note("Filtering candidates…")
        candidates = enrich_article_text(prefilter(articles, skip_reasons))
        candidates = [reclassify_article(a) for a in candidates]

        auto_pairs = []
        review_items = []
        human_review = get_settings().require_human_review

        note(f"Summarizing {len(candidates)} stories…")
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = [pool.submit(_summarize_one, article, human_review) for article in candidates]
            for fut in as_completed(futures):
                article, summary, flags, reason, _ = fut.result()
                if summary is None:
                    skip_reasons[reason] = skip_reasons.get(reason, 0) + 1
                    continue

                if flags:
                    if human_review:
                        review_items.append(
                            {
                                "url": summary.url,
                                "sport": summary.sport,
                                "headline": summary.headline,
                                "key_fact": summary.key_fact,
                                "why_it_matters": summary.why_it_matters,
                                "article_title": article.title,
                                "flags": ",".join(flags),
                            }
                        )
                        stats["pending"] += 1
                        continue

                    verdict, vreason = ai_verify_extreme(article, summary, flags)
                    if verdict != "PASS":
                        skip_reasons["ai_extreme_reject"] = skip_reasons.get("ai_extreme_reject", 0) + 1
                        log.info(
                            "skip ai_extreme_reject url=%s reason=%s",
                            article.url,
                            vreason,
                        )
                        continue
                    log.info("ai_extreme_pass url=%s reason=%s", article.url, vreason)

                auto_pairs.append((article, summary))

        note("Building world highlights…")
        summaries = finalize_by_relevance(auto_pairs)
        header, embeds, details = (
            build_embeds(summaries, datetime.now(TZ)) if summaries else ("", [], [])
        )
        sports = sorted({s.sport for s in summaries})
        stats["header"] = header
        stats["embeds"] = embeds
        stats["detail_embeds"] = details
        stats["sports_covered"] = sports
        stats["auto_items"] = [
            (s.url, s.headline, s.sport, s.headline) for s in summaries
        ]
        stats["review_items"] = review_items
        stats["skip_reasons"] = skip_reasons
        stats["skipped"] = sum(skip_reasons.values())
        stats["posted"] = len(summaries)
        log.info("world_digest stories=%s sports=%s dry_run=%s", len(summaries), sports, dry_run)
    except Exception as exc:
        log.exception("pipeline_failed")
        stats["error"] = type(exc).__name__
    return stats


async def maybe_await(fn, *args):
    if fn is None:
        return None
    result = fn(*args)
    if asyncio.iscoroutine(result):
        return await result
    return result


async def post_digest(
    send_pack: SendEmbeds,
    send_text: SendText,
    send_review: SendReview | None = None,
    *,
    notify_empty: bool = True,
    bypass_cache: bool = False,
    dry_run: bool = False,
    on_progress: ProgressFn | None = None,
) -> dict:
    loop = asyncio.get_running_loop()
    progress_box: list[str] = []

    def sync_progress(msg: str) -> None:
        progress_box.append(msg)
        if on_progress:
            fut = asyncio.run_coroutine_threadsafe(maybe_await(on_progress, msg), loop)
            try:
                fut.result(timeout=5)
            except Exception:
                pass

    stats = await asyncio.to_thread(
        build_digest,
        bypass_cache=bypass_cache,
        dry_run=dry_run,
        progress=sync_progress,
    )
    save_health_run(stats)

    if stats.get("error"):
        log.error("digest_aborted error=%s", stats["error"])
        return stats

    if send_review and not dry_run:
        for item in stats.get("review_items") or []:
            review_id = add_pending_review(item)
            item["id"] = review_id
            try:
                await maybe_await(send_review, item)
            except Exception:
                log.exception("review_send_failed")

    embeds = stats.get("embeds") or []
    details = stats.get("detail_embeds") or []
    header = stats.get("header") or ""
    if not embeds and not details:
        if notify_empty:
            await maybe_await(send_text, "No solid sports news from the last 24 hours.")
        else:
            log.info("daily_post_silent nothing_to_post")
        return stats

    await maybe_await(on_progress, "Posting to Discord…")
    await maybe_await(send_pack, header, embeds, details)

    if not dry_run:
        mark_posted_many(stats.get("auto_items") or [])
    log.info(
        "posted stories=%s pending=%s skipped=%s failures=%s dry_run=%s",
        stats["posted"],
        stats["pending"],
        stats["skipped"],
        len(stats.get("feed_failures") or []),
        dry_run,
    )
    return stats


def health_message(stats: dict | None = None) -> str:
    if stats is None:
        stats = {
            "fetched": 0,
            "skipped": 0,
            "posted": 0,
            "pending": 0,
            "feed_failures": [],
            "skip_reasons": {},
        }
    reasons = stats.get("skip_reasons") or {}
    reason_lines = "\n".join(f"- {k}: {v}" for k, v in sorted(reasons.items())) or "- none"
    fails = stats.get("feed_failures") or []
    fail_lines = "\n".join(f"- {u}" for u in fails) or "- none"
    sports = stats.get("sports_covered") or []
    sport_line = ", ".join(sports) if sports else "none"
    return (
        "**Daily digest health**\n"
        f"Fetched: {stats.get('fetched', 0)}\n"
        f"Posted: {stats.get('posted', 0)}\n"
        f"Sports covered: {sport_line}\n"
        f"Pending review: {stats.get('pending', 0)}\n"
        f"Skipped: {stats.get('skipped', 0)}\n"
        f"Skip reasons:\n{reason_lines}\n"
        f"Feed failures:\n{fail_lines}"
    )


def ping_ai_health() -> tuple[bool, str]:
    """Tiny Gemini/OpenAI check for startup health."""
    settings = get_settings()
    try:
        from digest.ai import call_gemini_raw, call_openai_raw

        if settings.gemini_api_key:
            raw = call_gemini_raw(
                "Reply with exactly: OK",
                settings.gemini_api_key,
                system_prompt="You are a health check. Reply OK only.",
                max_tokens=8,
            )
            return True, f"gemini:{raw[:40]}"
        if settings.openai_api_key:
            raw = call_openai_raw(
                "Reply with exactly: OK",
                settings.openai_api_key,
                settings.openai_base_url,
                system_prompt="You are a health check. Reply OK only.",
            )
            return True, f"openai:{raw[:40]}"
        return False, "no_ai_key"
    except Exception as exc:
        return False, type(exc).__name__
