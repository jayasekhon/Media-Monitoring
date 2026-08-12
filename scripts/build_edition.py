#!/usr/bin/env python3
"""
build_edition.py — the full pipeline, end to end.

    python scripts/build_edition.py [--date YYYY-MM-DD] [--out data/editions] [--no-llm]

Fetches free public sources (Google News RSS per theme — see fetch.py's
module docstring for why ReliefWeb was dropped), filters to the last 24
hours, classifies by region, and deduplicates near-identical stories —
all deterministic, no model involved.

Scoring, writing, and escalation-risk reasoning are then done by the
Gemini API (free tier, needs a GEMINI_API_KEY — see docs_GEMINI_SETUP.md)
when a key is available, region by region, with a rule-based fallback
(keyword scoring + extractive summarisation) used automatically if a
call fails or no key is present. Pass --no-llm to force pure rule-based
generation regardless of key availability.
"""
import argparse
import json
import sys
import time
from collections import defaultdict, Counter
from datetime import datetime, timezone
from pathlib import Path

from jsonschema import validate, ValidationError

from config import (
    REGION_ORDER, MAX_ITEMS_PER_REGION, MAX_ESCALATION_RISKS,
    LLM_CANDIDATE_POOL_SIZE, SOURCE_HIERARCHY, THEMES,
    MIN_KEYWORD_HITS_FOR_INCLUSION, CORROBORATION_SIMILARITY_THRESHOLD,
    MAX_SOURCES_PER_ITEM,
)
from fetch import fetch_all, enrich_items_with_article_text, resolve_published_source_links
from analyze import (
    is_blocklisted, classify_region, score_item, extract_verified_figures,
    dedup_items, is_hierarchy_source, title_similarity,
)
from summarize import (
    build_body, build_top_five_line, build_top_story_quote,
    build_risk_trigger_impact, confidence_from_source_count,
)
from llm_client import _get_token, LLMCallError
from llm_pipeline import build_region_reports_via_llm, build_synthesis_via_llm

ROOT = Path(__file__).resolve().parent.parent
SCHEMA_PATH = ROOT / "schema" / "edition.schema.json"

# Gap between consecutive LLM calls in the same run. Gemini's free tier
# can be as low as ~5-15 requests/minute depending on model, and our
# ~7 calls (one per region + synthesis) happen in quick succession
# otherwise — this spaces them out enough to stay under even a strict
# per-minute cap without relying entirely on retry/backoff to absorb it.
LLM_CALL_SPACING_SECONDS = 8


def build_region_reports_rule_based(candidates: list[dict]) -> list[dict]:
    """The original keyword-scoring + extractive-summary path, used as
    the automatic fallback when the LLM call for a region fails or is
    disabled. `candidates` should already be sorted best-first.
    """
    reports = []
    for it in candidates[:MAX_ITEMS_PER_REGION]:
        it["figures"] = extract_verified_figures(it["title"], it["description"])
        body, is_thin = build_body(it["title"], it["description"])
        headline = it["title"] if len(it["title"]) < 90 else it["title"][:87] + "..."
        reports.append({
            "headline": headline,
            "score": it["score"],
            "body": body,
            "verified_figures": it["figures"],
            "sources": it.get("sources") or [{"name": it["source"], "url": it.get("link", "")}],
            "_is_thin": is_thin,  # internal flag, stripped before writing
        })
    return reports


def build_synthesis_rule_based(sections: dict, all_used_items: list[dict]) -> dict:
    """The original cross-region top-five/top-story/escalation-risk
    logic, used as the fallback when the LLM synthesis call fails.
    """
    overall_sorted = sorted(all_used_items, key=lambda x: x["score"], reverse=True)

    if overall_sorted:
        top_five_source = overall_sorted[:5]
        while len(top_five_source) < 5:
            top_five_source.append(overall_sorted[len(top_five_source) % len(overall_sorted)])
        top_five = [
            {"rank": i + 1, "text": build_top_five_line(it["title"], it["description"]), "region": it["region"]}
            for i, it in enumerate(top_five_source[:5])
        ]
    else:
        top_five_source = []
        top_five = [
            {"rank": i + 1, "text": "No developments met inclusion criteria in the monitoring window, or source data was unavailable for this run.", "region": "Global / Cross-Cutting"}
            for i in range(5)
        ]

    if overall_sorted:
        top_item = overall_sorted[0]
        top_body, _ = build_body(top_item["title"], top_item["description"])
        top_story = {
            "score": top_item["score"],
            "quote": build_top_story_quote(top_body),
            "label": f"{top_item['region'].upper()} — {top_item['title'][:80]}",
        }
    else:
        top_story = {"score": 1.0, "quote": "No developments met inclusion criteria in the monitoring window.", "label": "NO SIGNIFICANT DEVELOPMENTS"}

    top_five_titles = {it["title"] for it in top_five_source[:5]}
    risk_candidates = [it for it in overall_sorted if it["score"] >= 7.0 and it["title"] not in top_five_titles]
    escalation_risks = []
    for it in risk_candidates[:MAX_ESCALATION_RISKS]:
        trigger, impact = build_risk_trigger_impact(it["title"], it["description"])
        risk_sources = it.get("sources") or [{"name": it["source"], "url": it.get("link", "")}]
        escalation_risks.append({
            "location": it["title"][:60],
            "score": it["score"],
            "trigger": trigger,
            "potential_impact": impact,
            "confidence": confidence_from_source_count(len(risk_sources)),
            "sources": risk_sources,
        })

    return {"top_five": top_five, "top_story": top_story, "escalation_risks": escalation_risks}


def corroborate_report_sources(report: dict, full_pool: list[dict]) -> int:
    """Scan `full_pool` — the region's FULL already-fetched-and-deduped
    candidate list, not just the smaller subset actually offered to the
    LLM (LLM_CANDIDATE_POOL_SIZE) — for additional priority-source
    coverage of the same story `report` covers, and append any found to
    report['sources']. No new network calls: this only re-examines data
    already fetched this run.

    Restricted to outlets in SOURCE_HIERARCHY (is_hierarchy_source) —
    the point is corroboration from outlets worth trusting, not padding
    the sources list with whatever else happened to be fetched.

    Returns how many additional sources were added.
    """
    existing_urls = {s.get("url", "") for s in report["sources"] if s.get("url")}
    existing_names = {s["name"] for s in report["sources"]}
    added = 0
    for candidate in full_pool:
        if len(report["sources"]) >= MAX_SOURCES_PER_ITEM:
            break
        link = candidate.get("link", "")
        if link and link in existing_urls:
            continue  # already counted as a source for this report
        if candidate["source"] in existing_names:
            continue
        if not is_hierarchy_source(candidate["source"]):
            continue
        if title_similarity(report["headline"], candidate["title"]) >= CORROBORATION_SIMILARITY_THRESHOLD:
            report["sources"].append({"name": candidate["source"], "url": link})
            existing_names.add(candidate["source"])
            if link:
                existing_urls.add(link)
            added += 1
    return added


def run(date_str: str, out_dir: Path, use_llm: bool = True):
    print(f"Building edition for {date_str}...")

    llm_available = False
    if use_llm:
        try:
            _get_token()
            llm_available = True
            print("GEMINI_API_KEY found — will attempt LLM-backed generation per region, with rule-based fallback.")
        except LLMCallError as e:
            print(f"LLM generation unavailable ({e}); using pure rule-based generation.")
    else:
        print("--no-llm passed; using pure rule-based generation.")

    raw_items, theme_region_map = fetch_all()
    print(f"Fetched {len(raw_items)} raw items before filtering.")

    filtered = [it for it in raw_items if it.get("title") and not is_blocklisted(it["title"])]

    enriched = []
    dropped_irrelevant = 0
    for it in filtered:
        default_region = theme_region_map.get(it["matched_theme"], "Global / Cross-Cutting")
        region = classify_region(it["title"], it["description"], default_region)
        score, matched_keywords = score_item(it["title"], it["description"])
        if len(matched_keywords) < MIN_KEYWORD_HITS_FOR_INCLUSION:
            dropped_irrelevant += 1
            continue
        it2 = dict(it)
        it2["region"] = region
        it2["score"] = score
        it2["matched_keywords"] = matched_keywords
        enriched.append(it2)
    if dropped_irrelevant:
        print(f"Dropped {dropped_irrelevant} item(s) with no matched severity keywords (likely false-positive search hits).")

    by_region = defaultdict(list)
    for it in enriched:
        by_region[it["region"]].append(it)

    candidate_pool_by_region = {}
    full_deduped_by_region = {}
    for region, items in by_region.items():
        deduped = dedup_items(items)
        deduped.sort(key=lambda x: x["score"], reverse=True)
        full_deduped_by_region[region] = deduped  # kept in full for the corroboration check later
        candidate_pool_by_region[region] = deduped[:LLM_CANDIDATE_POOL_SIZE]

    pool_items = [it for region in REGION_ORDER for it in candidate_pool_by_region.get(region, [])]
    enriched_count, enrichment_attempted = enrich_items_with_article_text(pool_items)
    if enrichment_attempted:
        print(f"Article-excerpt enrichment: {enriched_count}/{enrichment_attempted} succeeded.")

    sections = {r: [] for r in REGION_ORDER}
    all_used_items = []
    thin_body_count = 0
    region_method = {}
    first_llm_call = True
    for region in REGION_ORDER:
        candidates = candidate_pool_by_region.get(region, [])
        if not candidates:
            region_method[region] = "no candidates"
            sections[region] = []
            continue
        reports = None
        if llm_available:
            if not first_llm_call:
                time.sleep(LLM_CALL_SPACING_SECONDS)
            first_llm_call = False
            reports = build_region_reports_via_llm(region, candidates)
        if reports is not None:
            region_method[region] = "llm"
            for r in reports:
                r.setdefault("verified_figures", [])
            all_used_items.extend(candidates[:MAX_ITEMS_PER_REGION])
        else:
            region_method[region] = "rule-based" + (" (fallback)" if llm_available else "")
            reports = build_region_reports_rule_based(candidates)
            for r in reports:
                if r.pop("_is_thin", False):
                    thin_body_count += 1
            all_used_items.extend(candidates[:len(reports)])
        sections[region] = reports

    # Corroboration check — re-scan each region's full candidate pool
    # (not just what was offered to the LLM) for additional priority-
    # source coverage of each published item. No new network calls.
    corroborated_count = 0
    for region in REGION_ORDER:
        pool = full_deduped_by_region.get(region, [])
        for rep in sections[region]:
            corroborated_count += corroborate_report_sources(rep, pool)

    synthesis = None
    synthesis_method = "rule-based"
    if llm_available:
        if not first_llm_call:
            time.sleep(LLM_CALL_SPACING_SECONDS)
        sections_for_llm = {r: sections[r] for r in REGION_ORDER}
        synthesis = build_synthesis_via_llm(sections_for_llm)
    if synthesis is not None:
        synthesis_method = "llm"
    else:
        synthesis = build_synthesis_rule_based(sections, all_used_items)

    top_five = synthesis["top_five"]
    top_story = synthesis["top_story"]
    escalation_risks = synthesis["escalation_risks"]

    # Final link-resolution pass — every source URL actually being
    # published gets a resolution attempt here, including ones the
    # corroboration check just added a moment ago and never had a
    # chance to resolve. See resolve_published_source_links()'s
    # docstring for why this is separate from the earlier
    # enrich_items_with_article_text() pass rather than relying on it.
    resolved_link_count, resolved_link_attempted = resolve_published_source_links(sections, escalation_risks)
    if resolved_link_attempted:
        print(f"Published-link resolution: {resolved_link_count}/{resolved_link_attempted} succeeded.")

    all_sources_used = set()
    source_counter = Counter()
    for region in REGION_ORDER:
        for rep in sections[region]:
            for s in rep.get("sources", []):
                all_sources_used.add(s["name"])
                source_counter[s["name"]] += 1
    for risk in escalation_risks:
        for s in risk.get("sources", []):
            all_sources_used.add(s["name"])
            source_counter[s["name"]] += 1

    strongest = [
        s for s in SOURCE_HIERARCHY
        if any(s.lower() in used.lower() or used.lower() in s.lower() for used in all_sources_used)
    ]
    all_sources_breakdown = [
        {"name": name, "count": count}
        for name, count in sorted(source_counter.items(), key=lambda kv: (-kv[1], kv[0]))
    ]

    limitations = []
    if not raw_items:
        limitations.append(
            "No items were retrieved from any source this run (Google News RSS "
            "returned nothing or failed) — this edition reflects a source outage, not an absence of news."
        )
    empty_regions = [r for r in REGION_ORDER if not sections[r]]
    if empty_regions:
        limitations.append(
            f"No developments met inclusion thresholds in the monitoring window for: {', '.join(empty_regions)}."
        )
    no_figure_count = sum(1 for r in REGION_ORDER for rep in sections[r] if not rep.get("verified_figures"))
    if no_figure_count:
        limitations.append(f"{no_figure_count} item(s) had no extractable figures in available reporting.")
    if thin_body_count:
        limitations.append(
            f"{thin_body_count} item(s) fell back to rule-based generation with little real summary text "
            "available from source feeds — body text for these is limited to the headline."
        )
    if corroborated_count:
        limitations.append(
            f"Corroboration check added {corroborated_count} additional priority-source citation(s) to "
            "already-selected items, found by re-scanning material already fetched this run (no new "
            "network calls) — restricted to outlets in the source hierarchy."
        )
    if resolved_link_attempted:
        unresolved = resolved_link_attempted - resolved_link_count
        limitations.append(
            f"Published-source links: {resolved_link_count}/{resolved_link_attempted} Google News-derived "
            "link(s) resolved to a stable publisher URL."
            + (f" {unresolved} could not be resolved (the publisher blocked the request, or the underlying "
               "Google redirect itself was invalid) and still point at the original Google News link, which "
               "may not resolve reliably when clicked." if unresolved else "")
        )
    if llm_available:
        llm_regions = [r for r, m in region_method.items() if m == "llm"]
        fallback_regions = [r for r, m in region_method.items() if "fallback" in m]
        no_candidate_regions = [r for r, m in region_method.items() if m == "no candidates"]
        attempted = len(REGION_ORDER) - len(no_candidate_regions)
        limitations.append(
            f"Regional analysis and writing: {len(llm_regions)}/{attempted} region(s) with candidate material "
            f"generated via Gemini (LLM), {len(fallback_regions)} fell back to rule-based generation"
            + (f", {len(no_candidate_regions)} had no qualifying candidates to send." if no_candidate_regions else ".")
            + f" Cross-region synthesis (top five / top story / escalation risks): {synthesis_method}."
        )
    else:
        limitations.append(
            "This edition was generated entirely by rule-based keyword scoring and extractive "
            "summarisation (no LLM available this run) — treat scores and groupings as a first-pass "
            "triage, not finished analytical judgement."
        )

    source_notes = {
        "strongest_sources": strongest or ["No hierarchy-listed outlets surfaced in this window"],
        "all_sources": all_sources_breakdown,
        "limitations": limitations,
        "coverage_snapshot": {
            "outlets_reviewed": len(SOURCE_HIERARCHY),
            "themes_searched": len(THEMES),
            "items_considered": len(raw_items),
            "sources_cited": len(all_sources_used),
        },
        "internal_sources_confirmation": "Confirmed: no internal emails, internal files, or internal media monitoring products were used in this edition. All material was drawn from Google News RSS.",
    }

    for region in REGION_ORDER:
        for rep in sections[region]:
            rep.pop("_is_thin", None)

    edition = {
        "date": date_str,
        "printed_time": datetime.now(timezone.utc).strftime("%-I:%M %p UTC"),
        "top_five": top_five,
        "top_story": top_story,
        "sections": sections,
        "escalation_risks": escalation_risks,
        "source_notes": source_notes,
    }

    schema = json.loads(SCHEMA_PATH.read_text())
    try:
        validate(instance=edition, schema=schema)
    except ValidationError as e:
        print(f"::error::Generated edition failed schema validation: {e.message}")
        sys.exit(1)

    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{date_str}.json"
    out_path.write_text(json.dumps(edition, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Wrote {out_path}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=datetime.now(timezone.utc).strftime("%Y-%m-%d"))
    parser.add_argument("--out", default=str(ROOT / "data" / "editions"))
    parser.add_argument("--no-llm", action="store_true", help="Force pure rule-based generation even if GEMINI_API_KEY is set.")
    args = parser.parse_args()
    run(args.date, Path(args.out), use_llm=not args.no_llm)


if __name__ == "__main__":
    main()
