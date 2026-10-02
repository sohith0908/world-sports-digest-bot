from __future__ import annotations

import html
import re

TAG_RE = re.compile(r"<[^>]+>")
SCORELINE_RE = re.compile(
    r"\b([A-Z][\w'.-]+(?:\s+[A-Z][\w'.-]+){0,3})\s+(\d{1,3})\s*[-–]\s*(\d{1,3})\s+([A-Z][\w'.-]+(?:\s+[A-Z][\w'.-]+){0,3})\b"
)
BARE_SCORE_RE = re.compile(r"\b(\d{1,3})\s*[-–]\s*(\d{1,3})\b")


def clean_text(text: str) -> str:
    text = html.unescape(text or "")
    text = TAG_RE.sub("", text)
    return re.sub(r"\s+", " ", text).strip()


def extract_scoreline(text: str) -> str | None:
    """Return 'Team A 2-1 Team B' when present, else a bare score if found."""
    blob = clean_text(text)
    m = SCORELINE_RE.search(blob)
    if m:
        return f"{m.group(1)} {m.group(2)}-{m.group(3)} {m.group(4)}"
    m2 = BARE_SCORE_RE.search(blob)
    if m2:
        return f"{m2.group(1)}-{m2.group(2)}"
    return None
