#!/usr/bin/env python3
"""
build_edition.py — the whole no-AI pipeline, end to end.

    python scripts/build_edition.py [--date YYYY-MM-DD] [--out data/editions]

Fetches free public sources (Google News RSS per theme, ReliefWeb API),
filters to the last 24 hours, classifies by region, scores severity by
keyword, deduplicates near-identical stories, and writes a schema-valid
data/editions/YYYY-MM-DD.json. No AI/LLM calls anywhere in this script.
"""
import argparse
import json
import sys
from collections import defaultdict, Counter
from datetime import datetime, timezone
from pathlib import Path

from jsonschema import validate, ValidationError

from config import (
    REGION_ORDER, MAX_ITEMS_PER_REGION, MAX_ESCALATION_RISKS,
    SOURCE_HIERARCHY, THEMES, RELIEFWEB_QUERIES,
    MIN_KEYWORD_HITS_FOR_INCLUSION,
)
from fetch import fetch_all, enrich_items_with_article_text
from analyze import (
    is_blocklisted, classify_region, score_item, extract_verified_figures,
    dedup_items,
)
from summarize import (
    build_body, build_top_five_line, build_top_story_quote,
    build_risk_trigger_impact, confidence_from_source_count,
)

ROOT = Path(__file__).resolve().parent.parent
SCHEMA_PATH = ROOT / "schema" / "edition.schema.json"


def run(date_str: str, out_dir: Path):
    print(f"Building edition for {date_str}...")
    raw_items, theme_region_map = fetch_all()
    print(f"Fetched {len(raw_items)} raw items before filtering.")

    # Filter blocklisted / empty-title items
    filtered = [it for it in raw_items if it.get("title") and not is_blocklisted(it["title"])]

    # Classify region + score + extract figures
    enriched = []
    dropped_irrelevant = 0
    for it in filtered:
        default_region = theme_region_map.get(it["matched_theme"], "Global / Cross-Cutting")
        region = classify_region(it["title"], it["description"], default_region)
        score, matched_keywords = score_item(it["title"], it["description"])
        # Relevance floor: matched_keywords includes "[priority crisis]" when
        # that bonus applied, so len() here counts both real keyword hits
        # and the priority-crisis signal.
        if len(matched_keywords) < MIN_KEYWORD_HITS_FOR_INCLUSION:
            dropped_irrelevant += 1
            continue
        figures = extract_verified_figures(it["title"], it["description"])
        it2 = dict(it)
        it2["region"] = region
        it2["score"] = score
        it2["matched_keywords"] = matched_keywords
        it2["figures"] = figures
        enriched.append(it2)
    if dropped_irrelevant:
        print(f"Dropped {dropped_irrelevant} item(s) with no matched severity keywords (likely false-positive search hits).")

    # Dedup within each region (cross-region dedup would risk merging
    # genuinely distinct stories that happen to share vocabulary)
    by_region = defaultdict(list)
    for it in enriched:
        by_region[it["region"]].append(it)

    deduped_by_region = {}
    for region, items in by_region.items():
        deduped = dedup_items(items)
        deduped.sort(key=lambda x: x["score"], reverse=True)
        deduped_by_region[region] = deduped

    # Select the top N per region *before* doing any expensive per-item work
    # (article-excerpt fetching happens once, in one batched/concurrent pass,
    # only for the items that actually survive to publication — not for
    # every raw candidate that got filtered out earlier).
    selected_by_region = {
        region: deduped_by_region.get(region, [])[:MAX_ITEMS_PER_REGION]
        for region in REGION_ORDER
    }
    selected_items = [it for region in REGION_ORDER for it in selected_by_region[region]]

    enriched_count, enrichment_attempted = enrich_items_with_article_text(selected_items)
    if enrichment_attempted:
        print(f"Article-excerpt enrichment: {enriched_count}/{enrichment_attempted} succeeded.")

    # Build sections (formatted per schema)
    sections = {r: [] for r in REGION_ORDER}
    all_used_items = []
    thin_body_count = 0
    for region in REGION_ORDER:
        for it in selected_by_region[region]:
            # Re-extract figures now, after enrichment — the original
            # extraction ran on the pre-enrichment (often empty) Google
            # News description, so it would miss any casualty/displacement/
            # outbreak figures that only became available once the real
            # article text was fetched.
            it["figures"] = extract_verified_figures(it["title"], it["description"])
            body, is_thin = build_body(it["title"], it["description"])
            if is_thin:
                thin_body_count += 1
            headline = it["title"] if len(it["title"]) < 90 else it["title"][:87] + "..."
            sections[region].append({
                "headline": headline,
                "score": it["score"],
                "body": body,
                "verified_figures": it["figures"],
                "sources": it.get("sources") or [{"name": it["source"], "url": it.get("link", "")}],
            })
            all_used_items.append(it)

    # Top five: highest-scored items overall, one per cluster, across regions
    overall_sorted = sorted(all_used_items, key=lambda x: x["score"], reverse=True)

    if overall_sorted:
        top_five_source = overall_sorted[:5]
        while len(top_five_source) < 5:
            # pad by re-using lower-ranked items if fewer than 5 stories total
            top_five_source.append(overall_sorted[len(top_five_source) % len(overall_sorted)])
        top_five = [
            {"rank": i + 1, "text": build_top_five_line(it["title"], it["description"])}
            for i, it in enumerate(top_five_source[:5])
        ]
    else:
        # No items survived fetching/filtering (source outage, empty window,
        # or genuinely no qualifying developments). Don't fabricate content —
        # say so plainly in all five slots.
        top_five_source = []
        top_five = [
            {"rank": i + 1, "text": "No developments met inclusion criteria in the monitoring window, or source data was unavailable for this run."}
            for i in range(5)
        ]

    # Top story = #1 overall
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

    # Escalation risks: high-scoring items not already in top five, score >= 7
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

    # Source notes
    all_sources_used = set()
    source_counter = Counter()
    for region in REGION_ORDER:
        for rep in sections[region]:
            for s in rep["sources"]:
                all_sources_used.add(s["name"])
                source_counter[s["name"]] += 1
    for risk in escalation_risks:
        for s in risk["sources"]:
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
            "No items were retrieved from any source this run (Google News RSS and ReliefWeb both "
            "returned nothing or failed) — this edition reflects a source outage, not an absence of news."
        )
    empty_regions = [r for r in REGION_ORDER if not sections[r]]
    if empty_regions:
        limitations.append(
            f"No developments met inclusion thresholds in the monitoring window for: {', '.join(empty_regions)}."
        )
    no_figure_count = sum(1 for r in REGION_ORDER for rep in sections[r] if not rep["verified_figures"])
    if no_figure_count:
        limitations.append(
            f"{no_figure_count} item(s) had no extractable figures in available reporting; figures shown are limited to what source text stated explicitly."
        )
    if thin_body_count:
        limitations.append(
            f"{thin_body_count} item(s) had little or no real summary text available from source feeds "
            "(common with Google News RSS, which often provides only a headline with no article excerpt) "
            "— body text for these items is limited to the headline itself rather than a fuller extractive summary."
        )
    limitations.append(
        "This edition was generated by rule-based keyword scoring and extractive summarisation, not editorial judgement — "
        "treat scores and groupings as a first-pass triage, not a finished analytical assessment."
    )

    source_notes = {
        "strongest_sources": strongest or ["No hierarchy-listed outlets surfaced in this window"],
        "all_sources": all_sources_breakdown,
        "limitations": limitations,
        "coverage_snapshot": {
            "outlets_reviewed": len(SOURCE_HIERARCHY),
            "themes_searched": len(THEMES) + len(RELIEFWEB_QUERIES),
            "items_considered": len(raw_items),
            "sources_cited": len(all_sources_used),
        },
        "internal_sources_confirmation": "Confirmed: no internal emails, internal files, or internal media monitoring products were used in this edition. All material was drawn from Google News RSS and the public ReliefWeb API.",
    }

    edition = {
        "date": date_str,
        "printed_time": datetime.now(timezone.utc).strftime("%-I:%M %p UTC"),
        "top_five": top_five,
        "top_story": top_story,
        "sections": sections,
        "escalation_risks": escalation_risks,
        "source_notes": source_notes,
    }

    # Validate before writing
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
    args = parser.parse_args()
    run(args.date, Path(args.out))


if __name__ == "__main__":
    main()
