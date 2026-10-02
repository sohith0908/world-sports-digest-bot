from __future__ import annotations

import os
import re

from digest.ai import Summary, call_gemini_raw, call_openai_raw
from digest.logging_util import setup_logging
from digest.textutil import clean_text

log = setup_logging()

ROUNDUP_SYSTEM = """You write a worldwide daily sports highlights roundup.
Given sport-tagged story lines from yesterday, produce a tight global summary.

Output EXACTLY:
WORLD_HIGHLIGHTS:
- <Sport>: <one factual highlight, max 22 words>
(one bullet per distinct sport; cover every sport in the input; no intro/outro)
Rules: facts only from input; no invention; no emojis; preserve names/scores."""


def _bullet_fallback(summaries: list[Summary]) -> str:
    by_sport: dict[str, Summary] = {}
    for s in summaries:
        if s.sport in {"Medal Tally", "India Today", "World Highlights"}:
            continue
        cur = by_sport.get(s.sport)
        if cur is None or (s.india_relevant and not cur.india_relevant):
            by_sport[s.sport] = s
    # Stable: mega-events, then alpha for global scan feel.
    priority = {
        "Asian Games": 0,
        "Olympics": 1,
        "Football": 2,
        "Cricket": 3,
        "Tennis": 4,
        "NBA": 5,
        "NFL": 6,
        "F1": 7,
        "Rugby": 8,
        "Golf": 9,
    }
    sports = sorted(by_sport.keys(), key=lambda sp: (priority.get(sp, 50), sp.lower()))
    lines = []
    for sport in sports:
        s = by_sport[sport]
        fact = clean_text(s.key_fact)
        if len(fact.split()) > 22:
            fact = " ".join(fact.split()[:22]) + "."
        lines.append(f"• **{sport}**: {clean_text(s.headline)} — {fact}")
    return "\n".join(lines)


def _ai_roundup(summaries: list[Summary]) -> str | None:
    if not summaries:
        return None
    lines = []
    for s in summaries:
        if s.sport in {"Medal Tally", "India Today", "World Highlights"}:
            continue
        lines.append(f"[{s.sport}] {s.headline} | {s.key_fact}")
    if not lines:
        return None
    prompt = "Stories:\n" + "\n".join(lines[:24])
    gemini = os.getenv("GEMINI_API_KEY", "").strip()
    openai_key = os.getenv("OPENAI_API_KEY", "").strip()
    openai_base = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").strip()
    try:
        if gemini:
            raw = call_gemini_raw(prompt, gemini, system_prompt=ROUNDUP_SYSTEM, max_tokens=700)
        elif openai_key:
            raw = call_openai_raw(prompt, openai_key, openai_base, system_prompt=ROUNDUP_SYSTEM)
        else:
            return None
    except Exception:
        log.warning("world_roundup_ai_failed")
        return None

    body = raw or ""
    # Keep bullet lines only.
    bullets = []
    for line in body.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.upper().startswith("WORLD_HIGHLIGHTS"):
            continue
        line = re.sub(r"^[-*•]\s*", "• ", line)
        if not line.startswith("•"):
            line = "• " + line
        bullets.append(clean_text(line))
    return "\n".join(bullets[:16]) if bullets else None


def build_world_highlights_text(summaries: list[Summary]) -> str:
    """Worldwide one-pass highlight summary across every sport that made the digest."""
    ai_text = _ai_roundup(summaries)
    if ai_text and ai_text.count("•") >= 2:
        return ai_text
    return _bullet_fallback(summaries)


def sports_covered(summaries: list[Summary]) -> list[str]:
    seen = []
    for s in summaries:
        if s.sport in {"Medal Tally", "India Today", "World Highlights"}:
            continue
        if s.sport not in seen:
            seen.append(s.sport)
    return seen
