"""
analyze.py — the rule-based "editorial judgement" layer.

No model calls here. Every decision is a keyword match, a regex, or a
simple similarity score, so behaviour is fully auditable and reproducible.
This is the file that stands in for a human analyst's read-and-decide
step — treat its output as a first-pass triage, not finished analysis.
"""
import re
from difflib import SequenceMatcher

from config import (
    COUNTRY_REGION, PRIORITY_CRISIS_TERMS, KEYWORD_WEIGHTS, BASE_SCORE,
    PRIORITY_CRISIS_BONUS, MAX_SCORE, MIN_SCORE, BLOCKLIST_TERMS,
    SOURCE_HIERARCHY,
)

FIGURE_PATTERNS = [
    re.compile(r"\b\d[\d,\.]*\s*(?:percent|per cent)\b", re.IGNORECASE),
    re.compile(r"\b\d[\d,\.]*\s*(?:million|billion|thousand)\b(?:\s+\w+){0,3}", re.IGNORECASE),
    re.compile(r"\b\d[\d,]*\+?\s+(?:people|killed|dead|injured|wounded|displaced|deaths|cases|fatalities)\b", re.IGNORECASE),
]


def is_blocklisted(title: str) -> bool:
    t = title.lower()
    return any(term in t for term in BLOCKLIST_TERMS)


def classify_region(title: str, description: str, default_region: str) -> str:
    text = f"{title} {description}".lower()
    for place, region in COUNTRY_REGION.items():
        if place in text:
            return region
    return default_region


def score_item(title: str, description: str) -> tuple[float, list[str]]:
    """Returns (score, matched_keywords) for transparency/debugging."""
    text = f"{title} {description}".lower()
    score = BASE_SCORE
    matched = []

    for kw, weight in KEYWORD_WEIGHTS.items():
        if kw in text:
            score += weight
            matched.append(kw)

    if any(term in text for term in PRIORITY_CRISIS_TERMS):
        score += PRIORITY_CRISIS_BONUS
        matched.append("[priority crisis]")

    score = max(MIN_SCORE, min(MAX_SCORE, round(score, 1)))
    return score, matched


def extract_verified_figures(title: str, description: str, limit: int = 3) -> list[str]:
    text = f"{title}. {description}"
    found = []
    for pattern in FIGURE_PATTERNS:
        for m in pattern.finditer(text):
            phrase = m.group(0).strip()
            if phrase not in found:
                found.append(phrase)
            if len(found) >= limit:
                return found
    return found


STOPWORDS = {
    "the", "a", "an", "of", "in", "on", "as", "to", "for", "and", "with",
    "amid", "over", "after", "before", "is", "are", "was", "were", "at",
    "by", "from", "its", "it's", "un:", "un",
}


def _title_similarity(a: str, b: str) -> float:
    """Hybrid similarity: character-level ratio (catches near-identical
    wording) blended with word-overlap Jaccard on non-stopword tokens
    (catches same story worded differently, e.g. 'Gaza hunger crisis
    deepens as 67 percent face food insecurity' vs 'UN: 67 percent of
    Gazans face food insecurity amid ongoing crisis').
    """
    seq_ratio = SequenceMatcher(None, a.lower(), b.lower()).ratio()
    tokens_a = {w for w in a.lower().split() if w not in STOPWORDS and len(w) > 2}
    tokens_b = {w for w in b.lower().split() if w not in STOPWORDS and len(w) > 2}
    jaccard = len(tokens_a & tokens_b) / len(tokens_a | tokens_b) if (tokens_a or tokens_b) else 0.0
    return 0.55 * seq_ratio + 0.45 * jaccard


def dedup_items(items: list[dict], threshold: float = 0.42) -> list[dict]:
    """Cluster near-duplicate items by title similarity. Keeps the item
    from the highest-ranked source per SOURCE_HIERARCHY as the
    representative, and merges every cluster member's source name into
    `sources`.
    """
    clusters: list[dict] = []

    def source_rank(name: str) -> int:
        for i, s in enumerate(SOURCE_HIERARCHY):
            if s.lower() in name.lower():
                return i
        return len(SOURCE_HIERARCHY) + 1

    for item in items:
        placed = False
        for cluster in clusters:
            if _title_similarity(item["title"], cluster["title"]) >= threshold:
                cluster["members"].append(item)
                placed = True
                break
        if not placed:
            clusters.append({"title": item["title"], "members": [item]})

    deduped = []
    for cluster in clusters:
        members = cluster["members"]
        members_sorted = sorted(members, key=lambda m: source_rank(m["source"]))
        best = members_sorted[0]
        sources = []
        for m in members_sorted:
            if m["source"] not in sources:
                sources.append(m["source"])
        # Prefer the longest description among cluster members (more context)
        longest_desc = max((m["description"] for m in members), key=len, default="")
        merged = dict(best)
        merged["description"] = longest_desc or best["description"]
        merged["sources"] = sources[:4]
        deduped.append(merged)

    return deduped
