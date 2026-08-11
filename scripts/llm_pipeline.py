"""
llm_pipeline.py — the AI-backed judgment layer, built on the Gemini API.

This REPLACES the keyword-scoring and templated-writing parts of the
rule-based pipeline with real model calls that can paraphrase, judge
significance, and reason about escalation risk — closer to what the
original analyst prompt actually asked for. fetch.py's data-gathering
and analyze.py's region-classification/coarse-dedup stay exactly as
they were: cheap, deterministic, and genuinely don't need a model.

Design principle: every LLM call has a rule-based fallback. If a call
fails — rate limit, network error, malformed JSON, missing key — that
region (or the cross-region synthesis step) falls back to the existing
rule-based generation instead of breaking the whole run. A thin,
partially-rule-based edition beats a failed one.

Call budget: one call per non-empty region (up to 6) plus one synthesis
call — roughly 7 calls per run, comfortably inside Gemini's free daily
limits. See docs_GEMINI_SETUP.md.
"""
import json

from config import MAX_ITEMS_PER_REGION, MAX_ESCALATION_RISKS, SOURCE_HIERARCHY, REGION_ORDER
from llm_client import call_json, LLMCallError

# Condensed from prompts/EDITORIAL_POLICY.md's verbatim original prompt —
# keep the two in sync if you edit the analytical rules. Kept short
# deliberately: this gets sent on every single call, so bloating it
# wastes token budget that matters on a free tier.
#
# Note: SOURCE_HIERARCHY is interpolated in at call time (see below) so
# the model has the same outlet ranking the rule-based dedup step uses —
# without this, the model saw source names on candidate material but had
# no signal for which to trust more when they conflict or when only a
# weaker outlet covers something.
SYSTEM_POLICY_TEMPLATE = """You are a senior media analyst preparing a daily global media monitoring brief for senior humanitarian leadership, in the style of OCHA daily media monitoring emails.

Use ONLY the source material provided in the user message — never invent facts, figures, quotes, or developments not present in that material.

Source hierarchy — when multiple outlets cover the same development, or you must choose between conflicting figures, prefer information from higher-ranked outlets in this order: {hierarchy_list}. An unranked outlet isn't automatically unreliable, but don't let it override a higher-ranked source on the same fact.

Inclusion criteria: significant armed conflict, civilian casualties, major displacement, humanitarian access restrictions, food-security deterioration, disease outbreaks, natural disasters, climate emergencies, protection concerns, or developments with clear implications for humanitarian operations. Exclude routine politics, elections, business, sports, and entertainment unless they have direct, plausible humanitarian consequences.

For each development you include: consolidate duplicate reporting into a single item and write an original 40-120 word summary in your own words (never copy source text verbatim). Use plain, factual language, short sentences, and avoid jargon, advocacy, or hype. Include casualty, displacement, outbreak, or other impact figures only when explicitly stated in the source material. Clearly distinguish verified facts from claims, estimates, forecasts, or allegations. Note the strongest available source for each development. Maintain an authoritative, evidence-based tone grounded in verified data, official statistics, and objective analysis. Use impartial and neutral language consistent with humanitarian principles, avoiding political bias, speculation, and emotional sensationalism. Keep a human-centered perspective by highlighting impacts on people, especially vulnerable groups, while preserving dignity and avoiding reductive or overly dramatic framing. Use ONLY text from the sources to base your summary off of, taking account of the full context of each article.

Score each development 0-10 for HUMANITARIAN significance, not political salience: Watch 1-3, Serious 4-6, Severe 7-8, Critical 9-10. Critical should be used for any mass-casualty event or where a major natural disaster has occured, or similar.

Always prioritise, when present in the material: Iran/Israel/USA regional tensions, Gaza/OPT, Sudan, Ukraine, DRC, Syria, Yemen, Lebanon, Haiti, Myanmar, Afghanistan, Mali, Burkina Faso, the wider Sahel, major earthquakes/floods/tropical cyclones, and disease outbreaks (Ebola/cholera/mpox)."""

SYSTEM_POLICY = SYSTEM_POLICY_TEMPLATE.format(hierarchy_list=" > ".join(SOURCE_HIERARCHY))


def _region_user_prompt(region: str, candidates: list[dict]) -> str:
    material = [
        {
            "title": c["title"],
            "text": c.get("description", "") or "(no article text available — headline only)",
            "source": c["source"],
            "url": c.get("link", ""),
        }
        for c in candidates
    ]
    return f"""Region: {region}

Source material gathered from public news feeds in the last 24 hours (JSON array, each item has title/text/source/url):
{json.dumps(material, ensure_ascii=False)}

Select up to {MAX_ITEMS_PER_REGION} of the most significant, genuinely distinct developments for this region from the material above. Respond with ONLY a JSON object of exactly this shape, nothing else, no markdown fences:

{{"reports": [{{"headline": "Country/Event — short descriptor", "score": 7.5, "body": "your 40-120 word original summary", "verified_figures": ["figure as stated in source, e.g. '12,000 displaced'"], "sources": [{{"name": "Outlet Name", "url": "https://exact-article-url-from-material-above"}}]}}]}}

Use the exact "url" values from the material above in "sources" — don't invent or alter them. If nothing in the material meets inclusion criteria, respond with {{"reports": []}}."""


def build_region_reports_via_llm(region: str, candidates: list[dict]):
    """Returns a list of report dicts matching the schema's reportList
    shape, or None if the call failed (caller falls back to rule-based).
    """
    if not candidates:
        return []
    try:
        result = call_json(SYSTEM_POLICY, _region_user_prompt(region, candidates))
        reports = result.get("reports", [])
        for r in reports:
            assert isinstance(r["headline"], str) and r["headline"]
            assert isinstance(r["score"], (int, float))
            assert isinstance(r["body"], str) and r["body"]
            assert isinstance(r["verified_figures"], list)
            assert isinstance(r["sources"], list) and all("name" in s for s in r["sources"])
            r["score"] = max(0.0, min(10.0, float(r["score"])))
            for s in r["sources"]:
                s.setdefault("url", "")
        return reports[:MAX_ITEMS_PER_REGION]
    except (LLMCallError, KeyError, AssertionError, TypeError, ValueError) as e:
        print(f"::warning::LLM region call failed for '{region}', falling back to rule-based generation: {e}")
        return None


def _synthesis_user_prompt(sections: dict) -> str:
    return f"""Here are this edition's selected regional reports (JSON, one array per region):
{json.dumps(sections, ensure_ascii=False)}

Produce a JSON object with exactly these three keys, and nothing else, no markdown fences:

"top_five": array of exactly 5 objects {{"rank": 1-5, "text": "one sentence", "region": "exact region name from the JSON keys above"}} — the five most significant developments across ALL regions above, ranked by humanitarian significance. "region" must be one of the exact region-key strings used in the JSON above (e.g. "Middle East", "Asia-Pacific") so the site can link this item down to that section.

"top_story": {{"score": N, "quote": "one original sentence on the single most significant development, in your own words", "label": "REGION — short label"}}

"escalation_risks": array of up to {MAX_ESCALATION_RISKS} objects {{"location": "...", "score": N, "trigger": "one sentence describing what could trigger escalation in the next 72 hours", "potential_impact": "one sentence on the humanitarian impact if it happens", "confidence": "Low", "sources": [{{"name": "...", "url": "..."}}]}} — genuine forward-looking 72-hour risks distinct from top_five, using only outlets/URLs that appear in the reports above. Use an empty array if none are genuinely warranted — do not pad this to hit a count."""


def build_synthesis_via_llm(sections: dict):
    """Returns {"top_five": [...], "top_story": {...}, "escalation_risks": [...]}
    or None if the call failed (caller falls back to rule-based).
    """
    try:
        result = call_json(SYSTEM_POLICY, _synthesis_user_prompt(sections))
        assert isinstance(result["top_five"], list) and len(result["top_five"]) == 5
        assert isinstance(result["top_story"], dict) and "quote" in result["top_story"]
        assert isinstance(result["escalation_risks"], list)
        for item in result["top_five"]:
            # Model-provided region is a courtesy for deep-linking, not
            # something to trust blindly — fall back to the catch-all
            # region rather than fail the whole synthesis over one bad field.
            if item.get("region") not in REGION_ORDER:
                item["region"] = "Global / Cross-Cutting"
        for risk in result["escalation_risks"]:
            risk["score"] = max(0.0, min(10.0, float(risk.get("score", 5.0))))
            risk.setdefault("sources", [])
            if risk["confidence"] not in ("Low", "Medium", "High"):
                risk["confidence"] = "Medium"
        result["top_story"]["score"] = max(0.0, min(10.0, float(result["top_story"].get("score", 5.0))))
        return result
    except (LLMCallError, KeyError, AssertionError, TypeError, ValueError) as e:
        print(f"::warning::LLM synthesis call failed, falling back to rule-based generation: {e}")
        return None
