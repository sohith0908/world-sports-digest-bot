from __future__ import annotations

import os
import re
from datetime import datetime

import discord

from digest.ai import Summary
from digest.feeds import SPORT_COLORS, SPORT_EMOJI, TZ
from digest.textutil import clean_text
from digest.world_roundup import build_world_highlights_text, sports_covered

_MEDAL_TALLY_RE = re.compile(r"\bmedal tally|medallists?|medal table|medals?:\s*india\b", re.I)
_MEDAL_WIN_RE = re.compile(r"\b(gold|silver|bronze|medal)\b", re.I)


def section_order(summaries: list[Summary]) -> list[str]:
    """Mega-events first, then sections by breadth (more stories / global mix)."""
    grouped: dict[str, list[Summary]] = {}
    for item in summaries:
        grouped.setdefault(item.sport, []).append(item)

    def rank(sport: str) -> tuple[int, int, str]:
        if sport == "Asian Games":
            return (0, 0, sport)
        if sport == "Olympics":
            return (1, 0, sport)
        items = grouped[sport]
        return (2, -len(items), sport.lower())

    return sorted(grouped.keys(), key=rank)


def group_summaries(summaries: list[Summary]) -> dict[str, list[Summary]]:
    order = section_order(summaries)
    grouped: dict[str, list[Summary]] = {s: [] for s in order}
    for item in summaries:
        grouped.setdefault(item.sport, []).append(item)
    return {sport: grouped[sport] for sport in order if grouped.get(sport)}


def maybe_spoiler(text: str) -> str:
    if os.getenv("SPOILER_SCORES", "").strip() != "1":
        return text

    def repl(m):
        return f"||{m.group(0)}||"

    return re.sub(r"\b\d+\s*[-–]\s*\d+\b|\b\d{2,3}\*?\b|\bwon by [^.]+", repl, text, flags=re.I)


def build_header(day: datetime, count: int, sports: list[str] | None = None) -> str:
    sports = sports or []
    sport_line = ", ".join(sports) if sports else "no sports"
    return (
        f"**World Sports Highlights · {day:%d %b %Y}** · {count} stor{'y' if count == 1 else 'ies'}\n"
        f"Yesterday across the world: **{sport_line}**"
    )


def _story_block(item: Summary) -> str:
    headline = clean_text(item.headline)
    key_fact = maybe_spoiler(clean_text(item.key_fact))
    why = clean_text(item.why_it_matters) if item.why_it_matters else None
    block = [f"**{headline}**", key_fact]
    if why:
        block.append(f"Why it matters: {why}")
    block.append(f"[Read more]({item.url})")
    return "\n".join(block)


def _make_embed(title: str, description: str, color: int) -> discord.Embed:
    if len(description) > 4096:
        description = description[:4090] + "..."
    return discord.Embed(title=clean_text(title), description=description, color=color)


def build_world_highlights_embed(summaries: list[Summary]) -> discord.Embed | None:
    text = build_world_highlights_text(summaries)
    if not text.strip():
        return None
    covered = sports_covered(summaries)
    footer_note = f"Covered {len(covered)} sport(s)" if covered else ""
    desc = text if not footer_note else f"{text}\n\n_{footer_note}_"
    return _make_embed(
        f"{SPORT_EMOJI['World Highlights']} WORLD HIGHLIGHTS",
        desc,
        SPORT_COLORS["World Highlights"],
    )


def build_medal_tally_embed(summaries: list[Summary]) -> discord.Embed | None:
    tally = [
        s
        for s in summaries
        if _MEDAL_TALLY_RE.search(f"{s.headline} {s.key_fact}")
        or (
            s.sport == "Asian Games"
            and s.india_relevant
            and _MEDAL_WIN_RE.search(f"{s.headline} {s.key_fact}")
        )
    ]
    if not tally:
        return None
    tally.sort(
        key=lambda s: (
            0 if _MEDAL_TALLY_RE.search(f"{s.headline} {s.key_fact}") else 1,
            0 if s.india_relevant else 1,
        )
    )
    lines = []
    seen = set()
    for item in tally[:5]:
        key = item.headline.lower()[:60]
        if key in seen:
            continue
        seen.add(key)
        lines.append(
            f"• **{clean_text(item.headline)}** — {maybe_spoiler(clean_text(item.key_fact))}"
        )
        if item.url:
            lines[-1] += f" ([source]({item.url}))"
    if not lines:
        return None
    return _make_embed(
        f"{SPORT_EMOJI['Medal Tally']} MEDAL TALLY",
        "\n".join(lines),
        SPORT_COLORS["Medal Tally"],
    )


def build_india_today_embed(summaries: list[Summary]) -> discord.Embed | None:
    india = [
        s
        for s in summaries
        if s.india_relevant
        and s.sport
        in {
            "Asian Games",
            "Cricket",
            "Hockey",
            "Badminton",
            "Wrestling",
            "Boxing",
            "Athletics",
        }
    ]
    if not india:
        india = [s for s in summaries if s.india_relevant]
    if not india:
        return None
    india.sort(
        key=lambda s: (
            0 if s.sport == "Asian Games" else 1,
            0 if _MEDAL_WIN_RE.search(s.headline) else 1,
        )
    )
    lines = []
    seen = set()
    for item in india[:5]:
        key = item.headline.lower()[:60]
        if key in seen:
            continue
        seen.add(key)
        lines.append(
            f"• **{clean_text(item.headline)}** — {maybe_spoiler(clean_text(item.key_fact))}"
        )
    return _make_embed(
        f"{SPORT_EMOJI['India Today']} INDIA TODAY",
        "\n".join(lines),
        SPORT_COLORS["India Today"],
    )


def build_embeds(summaries: list[Summary], day: datetime | None = None) -> tuple[str, list[discord.Embed]]:
    day = day or datetime.now(TZ)
    embeds: list[discord.Embed] = []

    world = build_world_highlights_embed(summaries)
    if world:
        embeds.append(world)

    medal = build_medal_tally_embed(summaries)
    if medal:
        embeds.append(medal)
    india = build_india_today_embed(summaries)
    if india:
        embeds.append(india)

    grouped = group_summaries(summaries)
    # Leave room: world + optional medal/india already used slots (max 10 embeds).
    remaining = max(0, 10 - len(embeds))
    for sport, items in list(grouped.items())[:remaining]:
        description = "\n\n".join(_story_block(item) for item in items)
        emoji = SPORT_EMOJI.get(sport, "\U0001f3c6")
        embeds.append(
            _make_embed(
                f"{emoji} {sport.upper()}",
                description,
                SPORT_COLORS.get(sport, 0x546E7A),
            )
        )

    embeds = embeds[:10]
    header_sports = ["World Highlights"] if world else []
    if medal:
        header_sports.append("Medal Tally")
    if india:
        header_sports.append("India Today")
    header_sports.extend(list(grouped.keys())[:remaining])
    header = build_header(day, len(summaries), header_sports)
    if embeds:
        embeds[-1].timestamp = day
        embeds[-1].set_footer(text=f"World sports brief · {day:%d %b %Y} IST")
    return header, embeds
