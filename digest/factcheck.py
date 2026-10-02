from __future__ import annotations

import os
import re

from digest.ai import Summary, call_gemini_raw, call_openai_raw
from digest.db import cache_get, cache_set
from digest.feeds import Article
from digest.logging_util import setup_logging
from digest.quality import validate_summary_quality, why_is_weak
from digest.textutil import clean_text

log = setup_logging()

FACTCHECK_PROMPT = """You verify sports digest lines against article text only.

Answer whether every claim is supported by the article.

Output EXACTLY:
VERDICT: YES|NO
UNSUPPORTED: <comma-separated unsupported claims, or NONE>

Rules:
- Competition names, Women's/Men's labels, player names, team names, scores,
  and verdicts must match the article. If altered or invented, mark NO.
- If WHY IT MATTERS invents stakes not in the article, include it in UNSUPPORTED.
- If unsure, VERDICT: NO.
- Output nothing else."""


def parse_factcheck(raw: str) -> tuple[str, list[str]]:
    verdict_m = re.search(r"(?im)^VERDICT:\s*(YES|NO)\b", raw or "")
    unsupported_m = re.search(r"(?im)^UNSUPPORTED:\s*(.+)$", raw or "")
    verdict = verdict_m.group(1).upper() if verdict_m else "NO"
    unsupported_raw = unsupported_m.group(1).strip() if unsupported_m else "NONE"
    if unsupported_raw.upper() == "NONE" or not unsupported_raw:
        return verdict, []
    parts = [clean_text(p) for p in unsupported_raw.split(",") if clean_text(p)]
    return verdict, parts


def ai_factcheck(article: Article, summary: Summary) -> tuple[str, list[str]] | None:
    cache_key = f"fc:{summary.url}:{hash(summary.headline + summary.key_fact)}"
    cached = cache_get(cache_key)
    if cached and "verdict" in cached:
        return cached["verdict"], cached.get("unsupported", [])

    gemini = os.getenv("GEMINI_API_KEY", "").strip()
    openai_key = os.getenv("OPENAI_API_KEY", "").strip()
    openai_base = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").strip()
    why = summary.why_it_matters or "NONE"
    user = (
        f"article_text:\n{article.article_text[:3500]}\n\n"
        f"HEADLINE: {summary.headline}\n"
        f"KEY FACT: {summary.key_fact}\n"
        f"WHY IT MATTERS: {why}\n"
    )
    try:
        if gemini:
            raw = call_gemini_raw(user, gemini, system_prompt=FACTCHECK_PROMPT, max_tokens=180)
        elif openai_key:
            raw = call_openai_raw(user, openai_key, openai_base, system_prompt=FACTCHECK_PROMPT)
        else:
            return None
        verdict, unsupported = parse_factcheck(raw)
        cache_set(cache_key, {"verdict": verdict, "unsupported": unsupported})
        return verdict, unsupported
    except Exception:
        log.warning("factcheck_ai_failed url=%s", article.url)
        return None


def local_factcheck(article: Article, summary: Summary) -> tuple[str, list[str]]:
    unsupported = []
    for label, claim in (
        ("headline", summary.headline),
        ("key_fact", summary.key_fact),
        ("why", summary.why_it_matters or ""),
    ):
        if not claim:
            continue
        words = [w for w in re.findall(r"[a-z0-9']+", claim.lower()) if len(w) > 3]
        text = article.article_text.lower()
        hits = sum(1 for w in words if w in text)
        if words and hits / len(words) < 0.55:
            unsupported.append(label)
    return ("YES" if not unsupported else "NO"), unsupported


def factcheck_summary(article: Article, summary: Summary) -> tuple[Summary | None, list[str], str]:
    """
    Returns (summary_or_none, review_flags, skip_reason).
    review_flags non-empty means send to mod approve queue instead of auto-post.
    """
    headline = clean_text(summary.headline)
    key_fact = clean_text(summary.key_fact)
    why = clean_text(summary.why_it_matters) if summary.why_it_matters else None

    ok, why, flags, reason = validate_summary_quality(article, headline, key_fact, why)
    if not ok:
        return None, [], reason

    checked = Summary(
        headline=headline,
        key_fact=key_fact,
        why_it_matters=why,
        url=summary.url,
        sport=summary.sport,
        india_relevant=summary.india_relevant,
        competition=summary.competition,
    )

    result = ai_factcheck(article, checked)
    if result is None:
        verdict, unsupported = local_factcheck(article, checked)
    else:
        verdict, unsupported = result

    log.info(
        "factcheck url=%s verdict=%s unsupported=%s flags=%s",
        article.url,
        verdict,
        unsupported or ["NONE"],
        flags or ["NONE"],
    )

    if verdict != "YES":
        return None, [], f"factcheck_failed:{','.join(unsupported) or 'unsupported'}"

    if checked.why_it_matters and why_is_weak(
        checked.why_it_matters, checked.headline, checked.key_fact
    ):
        checked.why_it_matters = None

    return checked, flags, "ok"
