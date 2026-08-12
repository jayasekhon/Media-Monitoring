"""
config.py — the rule-based editorial policy.

Everything the original prompt expressed as instructions to a human/LLM
analyst, expressed here as data: which themes to search, which region a
theme or country belongs to, which keywords raise or lower a severity
score, and which outlets count as "strongest sources".

This is the file to edit if you want to tune behaviour — add a theme,
re-weight a keyword, add a country to a region, etc.
"""

# ---------------------------------------------------------------------------
# Search themes -> default region if no country override matches.
# Mirrors the "Also review public news aggregation/search results for the
# following themes" list in the original prompt, plus the extra watch items
# under "Also pay attention to developments involving".
# ---------------------------------------------------------------------------
THEMES = [
    ("Yemen humanitarian crisis", "Middle East"),
    ("Yemen food crisis", "Middle East"),
    ("Syria humanitarian crisis", "Middle East"),
    ("Syria displacement", "Middle East"),
    ("DRC conflict", "Africa"),
    ("DRC Ebola", "Africa"),
    ("Mali insecurity", "Africa"),
    ("Burkina Faso violence", "Africa"),
    ("Gaza humanitarian crisis", "Middle East"),
    ("West Bank violence", "Middle East"),
    ("OPT displacement", "Middle East"),
    ("Haiti gangs", "Americas"),
    ("Haiti displacement", "Americas"),
    ("Sudan humanitarian crisis", "Africa"),
    ("Sudan displacement", "Africa"),
    ("Sudan aid access", "Africa"),
    ("Myanmar conflict", "Asia-Pacific"),
    ("Myanmar displacement", "Asia-Pacific"),
    ("Afghanistan humanitarian crisis", "Asia-Pacific"),
    ("Lebanon displacement", "Middle East"),
    ("Ukraine conflict humanitarian impact", "Europe"),
    ("cholera outbreak", "Global / Cross-Cutting"),
    ("Ebola outbreak", "Global / Cross-Cutting"),
    ("mpox outbreak", "Global / Cross-Cutting"),
    ("major floods", "Global / Cross-Cutting"),
    ("major earthquake", "Global / Cross-Cutting"),
    ("tropical cyclone", "Global / Cross-Cutting"),
    ("climate emergency displacement", "Global / Cross-Cutting"),
    # "Also pay attention to" watch list — worded to match the original
    # prompt's specific framing (US/Iran regional escalation), not a
    # generic "Iran Israel tensions" which is a different, narrower thing.
    ("US Iran regional escalation", "Middle East"),
    ("Venezuela crisis", "Americas"),
    ("Strait of Hormuz shipping", "Global / Cross-Cutting"),
    # Added beyond the original prompt's own list, after real coverage
    # gaps surfaced against a live reference benchmark — Libya and
    # Somalia had genuine humanitarian-relevant stories (drone strikes on
    # oil infrastructure; a sharp rise in malnutrition admissions) with no
    # search path into this pipeline at all. Deliberately NOT added to
    # PRIORITY_CRISIS_TERMS below — that list stays faithful to the
    # original prompt's specific priority countries; these are additional
    # search coverage, not elevated to "always prioritise" status.
    ("Libya conflict", "Africa"),
    ("Somalia humanitarian crisis", "Africa"),
    # Added after reviewing 5 days of the manual reference digest —
    # Ethiopia (Aug 4 landslide, Aug 11 Tigray drone strikes) and Iraq/
    # Egypt (Aug 12 items) each appeared with no search path into this
    # pipeline at all. Ethiopia/Iraq were already in COUNTRY_REGION (so
    # they'd classify correctly once surfaced) but had nothing searching
    # for them; Egypt had neither. Same treatment as Libya/Somalia above:
    # NOT added to PRIORITY_CRISIS_TERMS below — additional search
    # coverage, not elevated to "always prioritise" status, since none of
    # the three were in the original prompt's priority list either.
    ("Ethiopia humanitarian crisis", "Africa"),
    ("Iraq security incident", "Middle East"),
    ("Egypt security incident", "Middle East"),
]

# ReliefWeb was dropped from this pipeline — its v1 API returns 410 Gone
# on every query as of this writing (see fetch.py's module docstring).

# ---------------------------------------------------------------------------
# Country / place name -> region. Checked against title+description text;
# first match wins region assignment (overrides the theme's default region).
# Keep keys lowercase. Order matters only where ambiguity exists.
# ---------------------------------------------------------------------------
COUNTRY_REGION = {
    # Middle East
    "gaza": "Middle East", "israel": "Middle East", "palestin": "Middle East",
    "west bank": "Middle East", "yemen": "Middle East", "syria": "Middle East",
    "lebanon": "Middle East", "iraq": "Middle East", "iran": "Middle East",
    "jordan": "Middle East", "houthi": "Middle East",
    # "Oman" added for correct classification if it surfaces via an
    # adjacent search (Hormuz shipping, Yemen) — no dedicated theme added
    # for it; the reference story that prompted this (an oil-tanker spill
    # reaching Oman's coast) is a genuinely borderline case against the
    # inclusion criteria (environmental/shipping incident, no clear direct
    # casualty/displacement angle), so search coverage wasn't added, just
    # correct region mapping if something does surface.
    "oman": "Middle East",
    # Egypt added alongside Oman for the same reason — it surfaced in the
    # manual reference digest (Damietta port drone/fire incident) with no
    # region mapping at all. Grouped as Middle East rather than Africa
    # (unlike Libya) since its humanitarian-relevant coverage in practice
    # tends to be Gaza/Red Sea/Suez-adjacent, not sub-Saharan-crisis-adjacent
    # — a judgment call, revisit if that assumption stops holding.
    "egypt": "Middle East",
    # Africa
    "sudan": "Africa", "darfur": "Africa", "el-obeid": "Africa",
    "congo": "Africa", "drc": "Africa", "kinshasa": "Africa", "libya": "Africa",
    "mali": "Africa", "burkina faso": "Africa", "somalia": "Africa",
    "ethiopia": "Africa", "tigray": "Africa", "nigeria": "Africa",
    "chad": "Africa", "niger": "Africa", "south sudan": "Africa",
    "kenya": "Africa", "uganda": "Africa", "sahel": "Africa",
    # Americas
    "haiti": "Americas", "venezuela": "Americas", "puerto rico": "Americas",
    "united states": "Americas", "u.s.": "Americas", "canada": "Americas",
    "mexico": "Americas", "colombia": "Americas", "ecuador": "Americas",
    # Europe
    "ukraine": "Europe", "russia": "Europe", "kyiv": "Europe",
    "odesa": "Europe", "poland": "Europe", "moldova": "Europe",
    # Asia-Pacific
    "myanmar": "Asia-Pacific", "afghanistan": "Asia-Pacific",
    "bangladesh": "Asia-Pacific", "philippines": "Asia-Pacific",
    "china": "Asia-Pacific", "india": "Asia-Pacific", "pakistan": "Asia-Pacific",
    "sri lanka": "Asia-Pacific", "indonesia": "Asia-Pacific",
}

# ---------------------------------------------------------------------------
# Priority crises get a scoring bonus regardless of raw keyword hits, per
# the prompt's "Always prioritise reporting related to" list.
#
# Caveat: because a priority-crisis match alone satisfies
# MIN_KEYWORD_HITS_FOR_INCLUSION below, broad country names like "israel"
# and "iran" here mean a story that merely mentions the country — with no
# actual severity keyword — can still pass the relevance floor. This is
# softened by the fact these terms are only ever checked against results
# from an already crisis-scoped Google News query (e.g. "US Iran regional
# escalation"), not a general feed, but it's a real tradeoff worth
# knowing about, not a non-issue.
# ---------------------------------------------------------------------------
PRIORITY_CRISIS_TERMS = [
    "gaza", "palestin", "israel", "iran", "sudan", "ukraine", "congo", "drc",
    "syria", "yemen", "lebanon", "haiti", "myanmar", "afghanistan", "mali",
    "burkina faso", "sahel", "ebola", "cholera", "mpox",
    # Major natural disasters get the same "always prioritise" bonus the
    # original prompt gives them alongside the named crisis countries —
    # they're already weighted individually in KEYWORD_WEIGHTS below, but
    # without this they weren't getting the extra priority-crisis bonus.
    "earthquake", "flood", "cyclone", "hurricane", "typhoon",
]

# ---------------------------------------------------------------------------
# Severity keyword weights (additive). Base score starts at BASE_SCORE.
# Deliberately simple and auditable rather than "clever" — every point in
# a story's score should be traceable to a specific matched phrase.
# ---------------------------------------------------------------------------
BASE_SCORE = 3.0
PRIORITY_CRISIS_BONUS = 1.5

KEYWORD_WEIGHTS = {
    # casualties
    "killed": 2.5, "dead": 2.0, "deaths": 2.5, "death toll": 2.5,
    "fatalities": 2.0, "massacre": 3.0,
    "injured": 1.5, "wounded": 1.5, "casualties": 2.0,
    # displacement / protection
    "displaced": 2.5, "displacement": 2.5, "evacuat": 2.0,
    "fled": 1.5, "refugee": 1.5, "forced return": 1.5,
    # hunger
    "famine": 3.0, "starvation": 3.0, "malnutrition": 2.0,
    "food insecurity": 2.0, "hunger": 1.5, "food crisis": 2.0,
    # health / outbreak
    "outbreak": 2.5, "cholera": 2.0, "ebola": 2.5, "mpox": 2.0,
    "epidemic": 2.0, "measles": 1.5,
    # conflict
    "ceasefire": 1.0, "offensive": 1.5, "strike": 1.5, "attack": 1.5,
    "shelling": 1.5, "airstrike": 2.0, "drone attack": 1.5,
    "clashes": 1.0, "insecurity": 1.0,
    # access
    "humanitarian access": 2.0, "blockade": 2.0, "siege": 2.0,
    "aid restrictions": 2.0, "aid cut": 1.5,
    # disasters / climate
    "earthquake": 2.0, "flood": 2.0, "cyclone": 2.0, "hurricane": 2.0,
    "typhoon": 2.0, "drought": 1.5, "wildfire": 1.5,
    # economic knock-ons with humanitarian relevance
    "fuel shortage": 1.0, "food prices": 1.0, "sanctions": 0.5,
    # protection concerns — previously entirely missing from this list
    # despite being an explicit inclusion category in the original
    # prompt. Without these, a story like "2.4 million girls barred from
    # school" scored nothing beyond the base + priority-crisis bonus,
    # losing out to any competing story that happened to use violence/
    # casualty language instead.
    "arrested": 1.5, "detained": 1.5, "detention": 1.5, "abducted": 2.0,
    "kidnapped": 2.0, "disappeared": 2.0, "torture": 2.5,
    "extrajudicial": 2.5, "human rights violation": 2.0,
    "denied education": 2.0, "barred from school": 2.0,
    "gender-based violence": 2.5, "forced conscription": 2.0,
    "arbitrary detention": 2.0,
}

MAX_SCORE = 10.0
MIN_SCORE = 1.0

# An item that matches a theme's search query but hits zero severity
# keywords and isn't a named priority crisis is very likely a false
# positive from the search (Google News queries are not perfectly
# precise) rather than a real humanitarian development. Require at
# least one matched keyword or the priority-crisis bonus to be included
# at all — this is a relevance floor, separate from the severity score
# itself, which still starts at BASE_SCORE for anything that clears it.
MIN_KEYWORD_HITS_FOR_INCLUSION = 1

# ---------------------------------------------------------------------------
# Source hierarchy — used to pick which outlet name to lead with when an
# item is corroborated by more than one, and to populate "strongest
# sources" in the footer.
# ---------------------------------------------------------------------------
SOURCE_HIERARCHY = [
    # "Associated Press" (not the short form "AP") — the short form would
    # false-match via the bidirectional substring check used elsewhere
    # against any source name containing those two letters together, e.g.
    # "Japan Times" or "NHK Japan". "AP News" is also listed separately —
    # that's how Google News' own title-suffix parsing typically labels
    # AP wire content via regular theme searches, and it doesn't
    # substring-match "Associated Press" any more than "AP" alone does.
    "Reuters", "AFP", "Associated Press", "AP News", "BBC", "Al Jazeera English", "Wall Street Journal",
    "France 24 English", "Financial Times", "The Guardian", "Washington Post",
    "The Economist", "The East African", "Xinhua English World", "Agencia EFE",
    # "ReliefWeb" removed — it was the now-dropped API integration's name,
    # not a real outlet byline. OCHA/UNICEF/WHO/UNHCR kept: these can
    # still legitimately appear as a Google News byline (e.g. a WHO press
    # statement or OCHA briefing that Google News indexes), which is
    # different from the ReliefWeb API integration itself.
    "OCHA", "UNICEF", "WHO", "UNHCR",
]

# ---------------------------------------------------------------------------
# Blocklist — titles containing these terms are dropped even if they
# matched a theme query (guards against Google News drifting off-topic).
#
# Honest limitation: this can never be as complete as the LLM path's
# natural-language exclusion judgement ("exclude routine politics,
# elections... unless direct humanitarian consequence"). A denylist can
# only catch terms someone thought to add — it has no concept of
# "routine" vs "consequential" the way real judgement does. Treat this
# as a coarse guard against obvious noise, not equivalent exclusion logic.
# ---------------------------------------------------------------------------
BLOCKLIST_TERMS = [
    "box office", "grammy", "oscar", "world cup group", "transfer window",
    "premier league table", "celebrity", "royal wedding", "film review",
    "album review", "stock market close", "earnings call", "quarterly profit",
    "election result", "election poll", "referendum result", "cabinet reshuffle",
    "parliamentary vote", "party primary", "leadership contest",
]

REGION_ORDER = ["Middle East", "Americas", "Africa", "Europe", "Asia-Pacific", "Global / Cross-Cutting"]
MAX_ITEMS_PER_REGION = 4
MAX_ESCALATION_RISKS = 4

# Corroboration check: after a report is finalized (by either the LLM or
# rule-based path), re-scan the region's full already-fetched candidate
# pool (not just the smaller LLM_CANDIDATE_POOL_SIZE subset that was
# actually offered to the LLM) for additional priority-source coverage
# of the same story, and append any found to that report's sources — no
# new network calls, since this only looks at material already fetched
# this run. Same similarity threshold as dedup by default (already tuned
# for "is this the same story"), exposed separately here in case it
# needs different tuning later.
CORROBORATION_SIMILARITY_THRESHOLD = 0.42
MAX_SOURCES_PER_ITEM = 4
MONITORING_WINDOW_HOURS = 24

# How many deduped candidates per region to hand the LLM to choose from —
# deliberately larger than MAX_ITEMS_PER_REGION so it has real editorial
# choice rather than just re-ranking whatever the keyword scorer already
# narrowed down to. Also used to bound the rule-based fallback's input
# pool and article-enrichment fetch volume, so this is the one knob that
# controls both paths' workload.
LLM_CANDIDATE_POOL_SIZE = 8
