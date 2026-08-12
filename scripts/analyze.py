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
    SOURCE_HIERARCHY, MAX_SOURCES_PER_ITEM,
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
    """Classify by country mention. Checks the TITLE alone first, and
    only falls back to the full title+description text if nothing
    matches there.

    This matters because COUNTRY_REGION is checked in a fixed order
    (first match wins), with Middle East entries defined first — a
    Colombia earthquake story that mentions "international rescue teams,
    including from Israel" in its body would otherwise match "israel"
    long before ever reaching "colombia", misclassifying a story that
    isn't about the Middle East at all. The title is far more likely to
    reflect the story's actual subject than a full body that may
    mention several countries in passing.
    """
    title_low = title.lower()
    for place, region in COUNTRY_REGION.items():
        if place in title_low:
            return region

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


def hierarchy_match_index(name: str):
    """Returns the SOURCE_HIERARCHY index `name` matches, or None if it
    doesn't match any hierarchy-listed outlet. Bidirectional substring
    check: outlets are often reported under a shortened byline (Google
    News gives "Al Jazeera", "France 24" rather than the fuller
    "Al Jazeera English", "France 24 English" used in the hierarchy
    list), so a one-directional check misses real matches and silently
    demotes major outlets to "unranked".
    """
    name_low = name.lower()
    for i, s in enumerate(SOURCE_HIERARCHY):
        s_low = s.lower()
        if s_low in name_low or name_low in s_low:
            return i
    return None


def is_hierarchy_source(name: str) -> bool:
    """Whether `name` matches any outlet in SOURCE_HIERARCHY — used by
    the corroboration check to restrict "additional sources" to
    priority-listed outlets rather than any outlet that happened to be
    fetched.
    """
    return hierarchy_match_index(name) is not None


def title_similarity(a: str, b: str) -> float:
    """Hybrid similarity: character-level ratio (catches near-identical
    wording) blended with word-overlap Jaccard on non-stopword tokens
    (catches same story worded differently, e.g. 'Gaza hunger crisis
    deepens as 67 percent face food insecurity' vs 'UN: 67 percent of
    Gazans face food insecurity amid ongoing crisis'). Used both for
    dedup (below) and for the corroboration check in build_edition.py.
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
        idx = hierarchy_match_index(name)
        return idx if idx is not None else len(SOURCE_HIERARCHY) + 1

    for item in items:
        placed = False
        for cluster in clusters:
            if title_similarity(item["title"], cluster["title"]) >= threshold:
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
        # The primary/representative source is always kept, regardless of
        # hierarchy status — it might be the only source for a story only
        # a minor/regional outlet caught (the exact coverage the outlet
        # RSS feeds were added to preserve, e.g. Typhoon Dolphin). But any
        # ADDITIONAL cluster members beyond the primary are only added if
        # they're hierarchy-listed — corroboration should come from
        # prioritised sources, not just whatever else happened to be
        # fetched. Mirrors corroborate_report_sources() in
        # build_edition.py, which applies the same restriction for
        # sources added after publication rather than at dedup time.
        sources = [{"name": best["source"], "url": best.get("link", "")}]
        seen_names = {best["source"]}
        for m in members_sorted[1:]:
            if m["source"] in seen_names:
                continue
            if not is_hierarchy_source(m["source"]):
                continue
            seen_names.add(m["source"])
            sources.append({"name": m["source"], "url": m.get("link", "")})
        # Prefer the longest description among cluster members (more context)
        longest_desc = max((m["description"] for m in members), key=len, default="")
        merged = dict(best)
        merged["description"] = longest_desc or best["description"]
        merged["sources"] = sources[:MAX_SOURCES_PER_ITEM]
        deduped.append(merged)

    return deduped
