from __future__ import annotations

import re
from difflib import SequenceMatcher

from digest.feeds import Article
from digest.logging_util import setup_logging
from digest.textutil import clean_text

log = setup_logging()

REJECT_TITLE = re.compile(
    r"\b("
    r"power rankings?|mailbag|things? i think|seven things|solak thinks|"
    r"predictions?|fantasy|odds|preview|mock draft|betting|gambling|"
    r"what if|column|opinion|hot take|look to|how to watch|gossip|"
    r"transfer rumours?|rumour round|buzz|panic|what to make of|"
    r"what we'?re hearing|meter|intel|resilience|purposeful skill|no different|"
    r"grading|what will|bombshell take|"
    r"guide to|storylines?|what to know|what we'?re watching|"
    r"training camps?|30(-|\s)?team|30 questions|"
    r"\bcte\b|grew up knowing|diagnose cte|here'?s why they|"
    r"patient zero|too shy for|meet [\w']+.+:|"
    r"in his own words|exclusive interview|"
    r"sportsworld|playoff picture|projections?|"
    r"breaking down the goalie|guidelines\b|"
    r"documentary|documentaries|"
    r"urges? .{0,40} improvement|come face to face|face[- ]?off|"
    r"press conference|news conference"
    r")\b",
    re.I,
)

NON_STORY = re.compile(
    r"\b("
    r"confirms? (the )?match will (go ahead|proceed|take place)|"
    r"match will (go ahead|proceed) as (planned|scheduled)|"
    r"fixture will go ahead|kick-?off (is|remains) at|how to follow|"
    r"guide to|biggest storylines|what to know|what we'?re watching|"
    r"dates to monitor|training camp guide|"
    r"\bcte\b|diagnose cte|cope with cte"
    r")\b",
    re.I,
)

CONCRETE_EVENT = re.compile(
    r"\b("
    r"\d+\s*[-–]\s*\d+|\d{2,3}\*?|"
    r"signed?|signs?|signing|contract|extension|"
    r"ruled out|injury|injured|surgery|suspended|banned|sacked|resigns?|"
    r"appointed|charged|guilty|verdict|fined|trade[ds]?|acquired|"
    r"won|wins|beat|beats|defeat(?:ed|s)?|draws?|drew|"
    r"century|hat-?trick|record sale|sold for|"
    r"gold|silver|bronze|medal"
    r")\b",
    re.I,
)

# Extreme sporting scores (avoid money like $12,260,000).
OUTLIER_SCORE = re.compile(r"(?<![\d,$])\b([2-9]\d{2})\*?\b(?!,?\d)")
OUTLIER_CHASE = re.compile(
    r"\b(3[5-9]\d|[4-9]\d{2})\b.{0,40}\b(chase|chased|target|total)\b|"
    r"\b(chase|chased|target|total)\b.{0,40}\b(3[5-9]\d|[4-9]\d{2})\b",
    re.I,
)
SENSITIVE = re.compile(
    r"\b("
    r"world record|record sale|guilty verdict|found guilty|charged with|"
    r"trade[ds]? to|ruled out|season-ending|surgery|banned for|suspended for"
    r")\b",
    re.I,
)


def is_question(text: str) -> bool:
    t = clean_text(text)
    return "?" in t or t.lower().startswith(("what ", "why ", "how ", "is ", "are ", "can ", "should "))


def why_restates_headline(headline: str, why: str) -> bool:
    return SequenceMatcher(None, headline.lower(), why.lower()).ratio() >= 0.72


def why_is_weak(why: str, headline: str, key_fact: str) -> bool:
    w = why.lower()
    if why_restates_headline(headline, why) or why_restates_headline(key_fact, why):
        return True
    if re.fullmatch(r".*\b(\d{1,2}\s+[a-z]{3,}|\d{1,2}:\d{2})\b.*", w) and not re.search(
        r"\b(lead|qualify|suspend|ban|fine|series|table|final|knockout)\b", w
    ):
        return True
    return False


def numbers_in_text(text: str) -> set[str]:
    return set(re.findall(r"\d{2,}", text))


def outlier_flags(text: str) -> list[str]:
    flags: list[str] = []
    for m in OUTLIER_SCORE.finditer(text):
        n = int(m.group(1))
        if n >= 180:
            flags.append(f"extreme_score:{n}")
    if OUTLIER_CHASE.search(text):
        flags.append("extreme_chase")
    if SENSITIVE.search(text):
        flags.append("sensitive_claim")
    return flags


def numbers_supported_by_article(claim: str, article_text: str) -> bool:
    claim_nums = numbers_in_text(claim)
    if not claim_nums:
        return True
    return claim_nums.issubset(numbers_in_text(article_text))


def title_is_opinion(title: str) -> bool:
    return bool(REJECT_TITLE.search(title))


def is_non_story(title: str, body: str = "") -> bool:
    return bool(NON_STORY.search(f"{title}\n{body}"))


def has_concrete_event(*parts: str) -> bool:
    return bool(CONCRETE_EVENT.search(" ".join(p for p in parts if p)))


def needs_human_review(headline: str, key_fact: str, why: str | None) -> list[str]:
    return outlier_flags(f"{headline} {key_fact} {why or ''}")


def validate_summary_quality(article: Article, headline: str, key_fact: str, why: str | None):
    headline = clean_text(headline)
    key_fact = clean_text(key_fact)
    why = clean_text(why) if why else None

    if is_question(key_fact) or is_question(headline):
        return False, None, [], "question_not_fact"
    if title_is_opinion(article.title) or title_is_opinion(headline):
        return False, None, [], "opinion_shape"
    if is_non_story(article.title, article.article_text) or is_non_story(headline, key_fact):
        return False, None, [], "non_story"
    if not has_concrete_event(headline, key_fact):
        return False, None, [], "no_concrete_event"

    combined = f"{headline} {key_fact} {why or ''}"
    if not numbers_supported_by_article(combined, article.article_text):
        return False, None, [], "numbers_not_in_article"

    if why and why_is_weak(why, headline, key_fact):
        why = None

    flags = needs_human_review(headline, key_fact, why)
    return True, why, flags, "ok"
