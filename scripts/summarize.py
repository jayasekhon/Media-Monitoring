"""
summarize.py — turns raw title/description text into the fields the
template needs. Everything here is extractive (pulled from source text)
or templated (assembled from fixed phrases + matched keywords) — there
is no generative writing. Where source text is too thin to fill a field
properly, we say so explicitly rather than inventing detail.
"""
import re

SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")


def clean_text(text: str) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    return text


def sentences(text: str) -> list[str]:
    text = clean_text(text)
    if not text:
        return []
    parts = [p.strip() for p in SENTENCE_SPLIT.split(text) if p.strip()]
    return parts


def build_body(title: str, description: str, min_words: int = 40, max_words: int = 120) -> str:
    """Extractive body: uses the description as-is (trimmed to the word
    budget at a sentence boundary). If the description is too short to
    meet the minimum, prepends the title as a lead sentence rather than
    fabricating content — this keeps every word traceable to source
    text, at the cost of sometimes running under 40 words.
    """
    desc = clean_text(description)
    title_clean = clean_text(title)

    parts = sentences(desc)
    if not parts and title_clean:
        parts = [title_clean if title_clean.endswith((".", "!", "?")) else title_clean + "."]

    combined = " ".join(parts)
    words = combined.split()

    if len(words) < min_words and title_clean and not combined.startswith(title_clean[:20]):
        combined = f"{title_clean.rstrip('.')}. {combined}".strip()
        words = combined.split()

    if len(words) > max_words:
        truncated = []
        count = 0
        for sent in sentences(combined):
            sent_words = sent.split()
            if count + len(sent_words) > max_words and truncated:
                break
            truncated.append(sent)
            count += len(sent_words)
        combined = " ".join(truncated) if truncated else " ".join(words[:max_words]) + "…"

    if len(combined.split()) < min_words:
        combined += " Further operational detail was not available in reporting retrieved for this edition."

    return combined


def build_top_five_line(title: str, description: str) -> str:
    """One sentence for the 'Five stories shaping the day' list."""
    parts = sentences(description) or [clean_text(title)]
    line = parts[0]
    if not line.endswith((".", "!", "?")):
        line += "."
    return line


def build_top_story_quote(body: str) -> str:
    parts = sentences(body)
    return parts[0] if parts else body


# --- Escalation-risk phrasing -------------------------------------------------

TRIGGER_TEMPLATES = {
    "outbreak": "Continued transmission and gaps in surveillance or treatment capacity in the area.",
    "displacement": "Renewed fighting or access denial forcing further population movement.",
    "conflict": "Continued armed clashes, strikes, or offensive operations in the area.",
    "disaster": "Continuing severe weather or aftershock activity following the initial event.",
    "access": "Further restriction or closure of humanitarian access routes.",
    "default": "Continuation or escalation of the conditions described in current reporting.",
}

IMPACT_TEMPLATES = {
    "outbreak": "Further spread could overwhelm treatment capacity and increase fatalities.",
    "displacement": "Additional large-scale displacement could strain shelter, food and protection services.",
    "conflict": "Further civilian harm and infrastructure damage could deepen humanitarian needs.",
    "disaster": "Further damage could increase casualties and delay emergency assistance.",
    "access": "Prolonged denial of access could worsen food, health and shelter outcomes for affected populations.",
    "default": "Conditions could materially worsen humanitarian needs in the area over the monitoring period.",
}

CATEGORY_KEYWORDS = {
    "outbreak": ["outbreak", "cholera", "ebola", "mpox", "epidemic", "measles"],
    "displacement": ["displaced", "displacement", "evacuat", "fled", "refugee"],
    "conflict": ["strike", "attack", "offensive", "shelling", "airstrike", "clashes", "massacre"],
    "disaster": ["earthquake", "flood", "cyclone", "hurricane", "typhoon", "wildfire", "drought"],
    "access": ["humanitarian access", "blockade", "siege", "aid restrictions", "aid cut"],
}


def categorize(text: str) -> str:
    text_low = text.lower()
    for category, keywords in CATEGORY_KEYWORDS.items():
        if any(kw in text_low for kw in keywords):
            return category
    return "default"


def build_risk_trigger_impact(title: str, description: str) -> tuple[str, str]:
    category = categorize(f"{title} {description}")
    return TRIGGER_TEMPLATES[category], IMPACT_TEMPLATES[category]


def confidence_from_source_count(n_sources: int) -> str:
    if n_sources >= 3:
        return "High"
    if n_sources == 2:
        return "Medium"
    return "Low"
