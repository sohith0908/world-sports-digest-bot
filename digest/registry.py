from __future__ import annotations

import re
from collections import Counter

# Higher = more important for an Indian Discord audience.
COMPETITIONS: list[tuple[re.Pattern[str], str, int]] = [
    (re.compile(r"\basian games\b|aichi[- ]?nagoya|nagoya 2026|asian-games", re.I), "Asian Games", 110),
    (re.compile(r"\bipl\b|indian premier league", re.I), "IPL", 100),
    (re.compile(r"\bindia\b.+\b(vs|v)\b|\b(vs|v)\b.+\bindia\b|team india|men in blue", re.I), "India internationals", 100),
    # Avoid matching "Rugby World Cup" as an ICC cricket event.
    (re.compile(r"\b(?<!rugby )((?:icc |cricket )?world cup|world test championship|\bwtc\b|champions trophy|asia cup)\b", re.I), "ICC events", 95),
    (re.compile(r"\brugby world cup\b", re.I), "Rugby World Cup", 88),
    (re.compile(r"\bolympics?\b", re.I), "Olympics", 90),
    (re.compile(r"\b(australian open|french open|wimbledon|us open)\b", re.I), "Grand Slams", 85),
    (re.compile(r"\bpremier league\b", re.I), "Premier League", 80),
    (re.compile(r"\buefa (women'?s )?champions league\b", re.I), "UEFA Champions League", 78),
    (re.compile(r"\bla liga\b|\bserie a\b|\bbundesliga\b", re.I), "Top EU leagues", 70),
    (re.compile(r"\bisl\b|indian super league", re.I), "ISL", 75),
    (re.compile(r"\bnba\b", re.I), "NBA", 38),
    (re.compile(r"\bnfl\b", re.I), "NFL", 35),
]

STAGE_BOOST = [
    (re.compile(r"\bfinal\b", re.I), 25),
    (re.compile(r"\bsemi-?finals?\b", re.I), 18),
    (re.compile(r"\bquarter-?finals?\b|\bknockout", re.I), 12),
]


def competition_score(text: str) -> tuple[int, str | None]:
    best = 0
    name = None
    for pattern, label, weight in COMPETITIONS:
        if pattern.search(text):
            if weight > best:
                best = weight
                name = label
    return best, name


def stage_score(text: str) -> int:
    total = 0
    for pattern, pts in STAGE_BOOST:
        if pattern.search(text):
            total += pts
    return total


def detect_unregistered_events(titles: list[str], min_count: int = 5) -> list[str]:
    """If a competition-like phrase appears in many headlines, surface it."""
    counter: Counter[str] = Counter()
    for title in titles:
        # Capture 2-4 word Proper-ish phrases ending in Cup/League/Open/Championship/Games.
        for m in re.finditer(
            r"\b([A-Z][\w']+(?:\s+[A-Z][\w']+){0,3}\s+(?:Cup|League|Open|Championship|Games|Trophy))\b",
            title,
        ):
            counter[m.group(1)] += 1
        for m in re.finditer(r"\b((?:Asian|Olympic|Premier|Champions)\s+\w+)\b", title, re.I):
            counter[m.group(1).title()] += 1
    return [name for name, count in counter.most_common(10) if count >= min_count]
