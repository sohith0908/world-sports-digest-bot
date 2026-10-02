from __future__ import annotations

import html
import re

TAG_RE = re.compile(r"<[^>]+>")


def clean_text(text: str) -> str:
    text = html.unescape(text or "")
    text = TAG_RE.sub("", text)
    return re.sub(r"\s+", " ", text).strip()
