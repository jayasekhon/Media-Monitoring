# Editorial policy — how the original prompt maps to code

This pipeline has no AI/LLM in it, so your original media-monitoring
prompt (reproduced in full at the bottom of this file) isn't fed to
anything at runtime. Instead, each of its instructions was translated
into a specific, auditable piece of code. This file is the map between
the two, so you can find and adjust the right place when you want to
change behaviour.

| Original instruction | Where it lives now |
|---|---|
| Outlet hierarchy (Reuters, AFP, BBC...) | `scripts/config.py` → `SOURCE_HIERARCHY`, used to pick the lead source in a dedup cluster and to populate "strongest sources" |
| Required themes to search | `scripts/config.py` → `THEMES`, queried against Google News RSS in `scripts/fetch.py` |
| Priority crises (Gaza, Sudan, Ukraine...) | `scripts/config.py` → `PRIORITY_CRISIS_TERMS`, adds a scoring bonus in `scripts/analyze.py` |
| Region grouping (Middle East, Africa...) | `scripts/config.py` → `COUNTRY_REGION`, applied in `analyze.classify_region()` |
| "X/Twitter only as early warning, must be corroborated" | Not implemented — this pipeline doesn't read X/Twitter at all, which trivially satisfies the constraint |
| Inclusion criteria (casualties, displacement, access, etc.) | `scripts/config.py` → `KEYWORD_WEIGHTS` keys; an item needs at least one match to be included at all (`MIN_KEYWORD_HITS_FOR_INCLUSION`) |
| Exclude routine politics/sports/entertainment | `scripts/config.py` → `BLOCKLIST_TERMS`, checked in `analyze.is_blocklisted()` |
| Consolidate duplicate reporting | `scripts/analyze.py` → `dedup_items()`, similarity-based clustering |
| Casualty/displacement/outbreak figures | `scripts/analyze.py` → `extract_verified_figures()`, regex-based |
| 40–120 words per item | `scripts/summarize.py` → `build_body()` |
| Escalation risks, next 72 hours | `scripts/build_edition.py`, items scoring ≥7 not already in the top five; trigger/impact text from `summarize.py` templates |
| Confidence level | `scripts/summarize.py` → `confidence_from_source_count()` — Low/Medium/High based on how many distinct outlets corroborate the item, not genuine analytical confidence |
| Source coverage notes / limitations | `scripts/build_edition.py`, assembled from what actually happened during the run (empty regions, missing figures, source outages) |
| "Confirm no internal sources used" | Hard-coded fixed string — trivially true since the pipeline only ever calls Google News RSS and the public ReliefWeb API |

## What this genuinely cannot do

Be honest with yourself about the gap between this and either the
original human-analyst prompt or an LLM-backed version:

- **No real judgement.** "Significant" here means "matched more keywords",
  not "an analyst decided this matters". A story can outscore a more
  important one just because its description happens to use more
  scoring-list words.
- **Extractive, not written.** Body text is source description text
  stitched together, occasionally with the headline prepended and a
  filler sentence appended if it's short. It is not synthesized
  narrative, and it will occasionally read awkwardly.
- **Escalation-risk text is templated**, not reasoned — trigger/impact
  phrasing comes from five fixed templates keyed on keyword category, not
  from actually thinking through what could happen next.
- **Dedup is similarity-based, not comprehension-based.** It will
  occasionally merge two genuinely distinct stories that share a lot of
  vocabulary, or miss a duplicate worded very differently.

If this gap turns out to matter for how the briefing gets used, the
Copilot Studio version documented in this repo's git history (or
regenerate on request) closes most of it without needing a paid API —
worth revisiting if quality becomes the priority over zero AI involvement.

---

## Original prompt (verbatim, for reference)

You are a senior media analyst preparing a daily global media monitoring
brief for senior leadership in a humanitarian organisation.

**Objective:** Review reporting published during the previous 24 hours
and produce a concise executive briefing in the style and utility of
OCHA daily media monitoring emails. Prepare before 9am CET time.

**Important source rule:** Use external news reporting and public
sources only. Do NOT use enterprise emails, internal media monitoring
products, internal OCHA compilations, internal files, internal chat
messages, or previous daily monitoring emails as sources.

**Monitoring window:** previous 24 hours, clearly stated at the top.

**Primary source hierarchy:** Reuters, AFP, BBC, Al Jazeera English, Wall
Street Journal, France 24 English, Financial Times, The Guardian World,
Washington Post, The Economist, The East African, Xinhua English World,
other credible international/regional/local media, public
humanitarian/institutional sources for figures/context.

**Required outlet review + themes:** see `scripts/config.py` → `THEMES`
for the full list, copied verbatim from the original prompt.

**X/social media rule:** early-warning only, must be independently
confirmed before inclusion.

**Priority crises:** Iran/Israel/USA, Gaza/OPT, Sudan, Ukraine, DRC,
Syria, Yemen, Lebanon, Haiti, Myanmar, Afghanistan, Mali, Burkina Faso,
Sahel, major earthquakes/floods/cyclones, Ebola/cholera/mpox outbreaks.
Also watch: US/Iran escalation, Lebanon, Venezuela, Ebola, Strait of
Hormuz disruption linked to humanitarian access/fuel/food prices.

**Inclusion criteria:** significant armed conflict, civilian casualties,
major displacement, humanitarian access restrictions, food-security
deterioration, disease outbreaks, natural disasters, climate emergencies,
protection concerns, significant international humanitarian response,
implications for UN/humanitarian operations.

**Exclude:** routine domestic politics, elections, business news, sports,
entertainment, celebrity news, routine diplomatic reporting — except
where they have direct or plausible humanitarian consequences.

**Analysis requirements:** consolidate duplicates, lead with the most
significant development, include casualty/displacement/outbreak figures
when available, distinguish verified facts from claims, neutral factual
language, don't copy article text, cite the strongest available source,
40–120 words per item.

**Output format:** MEDIA MONITORING header, date, Top Five Developments
to Watch, regional sections (Middle East / Americas / Africa / Europe /
Asia-Pacific / Global-Cross-Cutting), Potential Humanitarian Escalation
Risks (Location / Trigger / Potential Impact / Confidence), Source
Coverage Notes.

**Tone:** concise, neutral, factual, humanitarian-focused, operationally
relevant — professional daily media monitoring email for senior
humanitarian leadership.
