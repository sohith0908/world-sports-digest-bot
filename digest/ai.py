from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from difflib import SequenceMatcher

import httpx

from digest.db import cache_get, cache_set
from digest.feeds import Article
from digest.logging_util import setup_logging
from digest.textutil import clean_text

log = setup_logging()


def _similar(a: str, b: str) -> float:
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


SYSTEM_PROMPT = """You are the editor of a worldwide daily sports highlights digest.
Cover results and hard news from any sport, any country. Indian stories matter
when present, but do not ignore global football, tennis, F1, NBA, NFL, rugby, etc.

INPUT YOU WILL RECEIVE
- title, source, publish_date, article_text, sport, url

STEP 1: DECIDE IF THE ARTICLE QUALIFIES
Return exactly the word SKIP (nothing else) if ANY of these are true:
- Opinion/column/mailbag/power ranking/fantasy/odds/preview/buzz/panic/intel/guide/storylines.
- Only says a scheduled match will go ahead, with no new consequence.
- No concrete new fact (result, injury, transfer, record, verdict, signing, medal).
- article_text is empty or under 40 words.
- Key point would have to be phrased as a question.

STEP 2: WRITE THE SUMMARY
If it qualifies, output EXACTLY this format and nothing else:

HEADLINE: <max 16 words, plain language, no clickbait>
KEY FACT: <one sentence, max 28 words, specific detail>
WHY IT MATTERS: <consequence from the article only, max 20 words, or NONE>
INDIA_RELEVANT: yes|no

RULES
1. Use only facts in article_text. Never invent.
2. Preserve competition names, Women's/Men's labels, player names, and team
   names exactly as the article uses them.
3. Never repeat the headline in KEY FACT or WHY IT MATTERS.
4. WHY IT MATTERS must be a consequence (series lead, qualification, ban,
   table position), never a date, scorer, or headline restatement. Else NONE.
5. No emojis, markdown, hype words, or quotes.
6. Write as a global sports wire highlight, not a local-only blurb.
7. Output nothing before or after the required format."""


@dataclass
class Summary:
    headline: str
    key_fact: str
    why_it_matters: str | None
    url: str
    sport: str
    india_relevant: bool = False
    competition: str | None = None


def _user_payload(article: Article) -> str:
    return (
        f"title: {article.title}\n"
        f"source: {article.source}\n"
        f"publish_date: {article.publish_date.isoformat()}\n"
        f"sport: {article.sport}\n"
        f"url: {article.url}\n"
        f"article_text: {article.article_text[:3500]}"
    )


def parse_summary(text: str, article: Article) -> Summary | None:
    raw = (text or "").strip()
    if not raw or raw.upper().startswith("SKIP"):
        log.info("skip ai_skip url=%s", article.url)
        return None

    headline_m = re.search(r"(?im)^HEADLINE:\s*(.+)$", raw)
    key_m = re.search(r"(?im)^KEY FACT:\s*(.+)$", raw)
    why_m = re.search(r"(?im)^WHY IT MATTERS:\s*(.+)$", raw)
    india_m = re.search(r"(?im)^INDIA_RELEVANT:\s*(yes|no)\b", raw)

    if not headline_m or not key_m:
        log.info("skip missing_fields url=%s", article.url)
        return None

    headline = clean_text(headline_m.group(1))
    key_fact = clean_text(key_m.group(1))
    why = clean_text(why_m.group(1)) if why_m else "NONE"
    india = bool(india_m and india_m.group(1).lower() == "yes")

    if why.upper() == "NONE" or not why:
        why_val = None
    else:
        why_val = why

    if _similar(headline, key_fact) >= 0.85:
        return None

    return Summary(
        headline=headline[:140],
        key_fact=key_fact[:240],
        why_it_matters=why_val[:180] if why_val else None,
        url=article.url,
        sport=article.sport,
        india_relevant=india or bool(article.india_relevant),
        competition=article.competition,
    )


def call_gemini_raw(
    prompt: str,
    api_key: str,
    *,
    system_prompt: str,
    max_tokens: int = 1024,
) -> str:
    preferred = os.getenv("GEMINI_MODEL", "").strip()
    models = [
        m
        for m in [
            preferred,
            "gemini-flash-lite-latest",
            "gemini-flash-latest",
            "gemini-2.5-flash-lite",
        ]
        if m
    ]
    payload = {
        "system_instruction": {"parts": [{"text": system_prompt}]},
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.1, "maxOutputTokens": max_tokens},
    }
    headers = {"x-goog-api-key": api_key, "Content-Type": "application/json"}
    last_error: Exception | None = None
    with httpx.Client(timeout=30.0) as client:
        for model in models:
            url = (
                "https://generativelanguage.googleapis.com/v1beta/models/"
                f"{model}:generateContent"
            )
            try:
                resp = client.post(url, headers=headers, json=payload)
                if resp.status_code == 404:
                    continue
                resp.raise_for_status()
                parts = resp.json()["candidates"][0]["content"]["parts"]
                return "".join(p.get("text", "") for p in parts).strip()
            except Exception as exc:
                last_error = exc
    raise RuntimeError(f"Gemini failed: {type(last_error).__name__ if last_error else 'unknown'}")


def call_openai_raw(
    prompt: str,
    api_key: str,
    base_url: str,
    *,
    system_prompt: str,
) -> str:
    endpoint = base_url.rstrip("/") + "/chat/completions"
    payload = {
        "model": os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
        "temperature": 0.1,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ],
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    with httpx.Client(timeout=30.0) as client:
        resp = client.post(endpoint, headers=headers, json=payload)
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"].strip()


def local_summary(article: Article) -> Summary | None:
    words = article.article_text.split()
    if len(words) < 30:
        return None
    sentences = [
        s.strip()
        for s in re.split(r"(?<=[.!?])\s+", article.article_text)
        if s.strip() and len(s.split()) >= 5
    ]
    if not sentences:
        return None
    headline = " ".join(article.title.split()[:16])
    key = sentences[0]
    if len(key.split()) > 28:
        key = " ".join(key.split()[:28]) + "."
    india = bool(re.search(r"\bindia|indian|kohli|gill|rohit|bumrah\b", f"{article.title} {article.article_text}", re.I))
    return Summary(
        headline=clean_text(headline),
        key_fact=clean_text(key),
        why_it_matters=None,
        url=article.url,
        sport=article.sport,
        india_relevant=india,
        competition=article.competition,
    )


def summarize_article(article: Article) -> tuple[Summary | None, list[str], str]:
    from digest.factcheck import factcheck_summary

    cache_key = "sum:" + hashlib.sha256(
        f"{article.url}:{article.article_text[:500]}".encode()
    ).hexdigest()
    cached = cache_get(cache_key)
    if cached and cached.get("headline"):
        summary = Summary(
            headline=cached["headline"],
            key_fact=cached["key_fact"],
            why_it_matters=cached.get("why_it_matters"),
            url=article.url,
            sport=article.sport,
            india_relevant=bool(cached.get("india_relevant")),
            competition=article.competition,
        )
        return factcheck_summary(article, summary)

    prompt = _user_payload(article)
    gemini = os.getenv("GEMINI_API_KEY", "").strip()
    openai_key = os.getenv("OPENAI_API_KEY", "").strip()
    openai_base = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").strip()

    summary = None
    try:
        if gemini:
            raw = call_gemini_raw(prompt, gemini, system_prompt=SYSTEM_PROMPT)
            summary = parse_summary(raw, article)
        elif openai_key:
            raw = call_openai_raw(prompt, openai_key, openai_base, system_prompt=SYSTEM_PROMPT)
            summary = parse_summary(raw, article)
    except Exception:
        log.warning("ai_failed url=%s", article.url)

    if summary is None:
        summary = local_summary(article)
    if summary is None:
        return None, [], "summarize_failed"

    cache_set(
        cache_key,
        {
            "headline": summary.headline,
            "key_fact": summary.key_fact,
            "why_it_matters": summary.why_it_matters,
            "india_relevant": summary.india_relevant,
        },
    )
    return factcheck_summary(article, summary)
