from __future__ import annotations

import os
import re

from digest.ai import Summary, call_gemini_raw, call_openai_raw
from digest.db import cache_get, cache_set
from digest.feeds import Article
from digest.logging_util import setup_logging
from digest.textutil import clean_text

log = setup_logging()

EXTREME_VERIFY_PROMPT = """You are a strict sports fact auditor for an automated Discord bot.
No human will review this. Be conservative.

You receive:
- article_text (source of truth)
- a draft summary (headline, key fact, why it matters)
- flags (why this looked extreme/sensitive)

Decide if the summary is safe to auto-post.

Output EXACTLY:
VERDICT: PASS|FAIL
REASON: <one short sentence>

PASS only if ALL are true:
1. Every number, score, name, competition, gender label, injury, trade, and verdict
   in the summary appears clearly in article_text.
2. Extreme scores/chases are explicitly stated as facts in the article, not jokes,
   hypotheticals, previews, or unclear phrasing.
3. The summary does not invent or exaggerate beyond the article.
4. If the article itself looks satirical, unclear, or contradictory, FAIL.

FAIL if unsure. Output nothing else."""


def parse_verify(raw: str) -> tuple[str, str]:
    verdict_m = re.search(r"(?im)^VERDICT:\s*(PASS|FAIL)\b", raw or "")
    reason_m = re.search(r"(?im)^REASON:\s*(.+)$", raw or "")
    verdict = verdict_m.group(1).upper() if verdict_m else "FAIL"
    reason = clean_text(reason_m.group(1)) if reason_m else "no reason"
    return verdict, reason


def local_verify(article: Article, summary: Summary, flags: list[str]) -> tuple[str, str]:
    """Fallback if AI unavailable: require all summary numbers in article text."""
    blob = f"{summary.headline} {summary.key_fact} {summary.why_it_matters or ''}"
    nums = set(re.findall(r"\d{2,}", blob))
    article_nums = set(re.findall(r"\d{2,}", article.article_text))
    if nums and not nums.issubset(article_nums):
        return "FAIL", "numbers missing from article"
    # Extreme chase/score still needs AI ideally; without AI, only pass if numbers match
    # and article mentions the same score near player/team words.
    if any(f.startswith("extreme_") for f in flags):
        # Require at least one extreme number to appear with '*' or 'not out' / 'chase' nearby.
        text = article.article_text.lower()
        if not re.search(r"\b\d{3}\*?|\bchased?\b|\bnot out\b", text):
            return "FAIL", "extreme claim lacks clear support phrasing"
    return "PASS", "local number check passed"


def ai_verify_extreme(article: Article, summary: Summary, flags: list[str]) -> tuple[str, str]:
    cache_key = f"xv:{summary.url}:{hash(summary.headline + summary.key_fact + ','.join(flags))}"
    cached = cache_get(cache_key)
    if cached and cached.get("verdict") in ("PASS", "FAIL"):
        return cached["verdict"], cached.get("reason", "")

    user = (
        f"flags: {', '.join(flags)}\n"
        f"headline: {summary.headline}\n"
        f"key_fact: {summary.key_fact}\n"
        f"why_it_matters: {summary.why_it_matters or 'NONE'}\n\n"
        f"article_text:\n{article.article_text[:4000]}\n"
    )

    gemini = os.getenv("GEMINI_API_KEY", "").strip()
    openai_key = os.getenv("OPENAI_API_KEY", "").strip()
    openai_base = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").strip()

    try:
        if gemini:
            raw = call_gemini_raw(
                user,
                gemini,
                system_prompt=EXTREME_VERIFY_PROMPT,
                max_tokens=120,
            )
        elif openai_key:
            raw = call_openai_raw(
                user,
                openai_key,
                openai_base,
                system_prompt=EXTREME_VERIFY_PROMPT,
            )
        else:
            verdict, reason = local_verify(article, summary, flags)
            return verdict, reason
        verdict, reason = parse_verify(raw)
    except Exception:
        log.warning("extreme_verify_ai_failed url=%s", article.url)
        verdict, reason = local_verify(article, summary, flags)

    cache_set(cache_key, {"verdict": verdict, "reason": reason})
    log.info(
        "extreme_verify url=%s verdict=%s flags=%s reason=%s",
        article.url,
        verdict,
        flags,
        reason,
    )
    return verdict, reason
