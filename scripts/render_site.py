#!/usr/bin/env python3
"""
Render The Daily Belle site from JSON editions in data/editions/*.json.

Usage:
    python scripts/render_site.py

Reads every data/editions/YYYY-MM-DD.json, validates it against
schema/edition.schema.json, and writes:
    docs/index.html                          (latest edition, also copied)
    docs/editions/YYYY-MM-DD/index.html       (one per edition)
    docs/feed.xml                             (RSS, last 30 editions)
    docs/static/style.css                     (copied in)

Designed to be run by GitHub Actions on every push to data/editions/**.
"""
import json
import shutil
from datetime import datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader
from jsonschema import validate, ValidationError
from feedgen.feed import FeedGenerator

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data" / "editions"
SCHEMA_PATH = ROOT / "schema" / "edition.schema.json"
TEMPLATE_DIR = ROOT / "templates"
STATIC_DIR = ROOT / "static"
DOCS_DIR = ROOT / "docs"

REGION_ORDER = ["Middle East", "Americas", "Africa", "Europe", "Asia-Pacific", "Global / Cross-Cutting"]

SITE_TITLE = "OCHA Mediaa Monitoring"
SITE_URL = "https://example.github.io/daily-belle/"  # overwritten by build_config.json if present


def score_class(score: float) -> str:
    if score >= 9:
        return "sc-critical"
    if score >= 7:
        return "sc-severe"
    if score >= 4:
        return "sc-serious"
    return "sc-watch"


def load_editions():
    schema = json.loads(SCHEMA_PATH.read_text())
    editions = []
    for f in sorted(DATA_DIR.glob("*.json")):
        try:
            data = json.loads(f.read_text())
            validate(instance=data, schema=schema)
        except (json.JSONDecodeError, ValidationError) as e:
            print(f"::warning::Skipping invalid edition file {f.name}: {e}")
            continue
        editions.append(data)
    editions.sort(key=lambda e: e["date"])
    return editions


def date_display(date_str: str) -> str:
    d = datetime.strptime(date_str, "%Y-%m-%d")
    return d.strftime("%A, %B %-d, %Y") if hasattr(d, "strftime") else date_str


def build_site_root(depth: int) -> str:
    """Relative path prefix back to docs/ root, depending on nesting depth."""
    return "../" * depth if depth else "./"


def main():
    site_config_path = ROOT / "build_config.json"
    site_url = SITE_URL
    if site_config_path.exists():
        cfg = json.loads(site_config_path.read_text())
        site_url = cfg.get("site_url", SITE_URL)

    editions = load_editions()
    if not editions:
        print("No valid editions found in data/editions/. Nothing to render.")
        return

    for e in editions:
        e["date_display"] = date_display(e["date"])
        e["date_display_upper"] = e["date_display"].upper()

    env = Environment(loader=FileSystemLoader(str(TEMPLATE_DIR)), autoescape=False)
    env.globals["score_class"] = score_class
    template = env.get_template("edition.html.jinja")

    # Clean and recreate docs/
    if DOCS_DIR.exists():
        shutil.rmtree(DOCS_DIR)
    DOCS_DIR.mkdir(parents=True)
    (DOCS_DIR / "static").mkdir(parents=True)
    (DOCS_DIR / "editions").mkdir(parents=True)
    shutil.copy(STATIC_DIR / "style.css", DOCS_DIR / "static" / "style.css")

    # Build dropdown option list, newest first
    all_editions_desc = [
        {
            "date": e["date"],
            "label": datetime.strptime(e["date"], "%Y-%m-%d").strftime("%b %-d, %Y"),
            "url": f"../editions/{e['date']}/",
            "is_latest": False,
        }
        for e in reversed(editions)
    ]
    all_editions_desc[0]["is_latest"] = True

    for idx, edition in enumerate(editions):
        older = editions[idx - 1] if idx > 0 else None
        newer = editions[idx + 1] if idx < len(editions) - 1 else None

        out_dir = DOCS_DIR / "editions" / edition["date"]
        out_dir.mkdir(parents=True, exist_ok=True)

        html = template.render(
            edition=edition,
            region_order=REGION_ORDER,
            older_url=f"../{older['date']}/" if older else None,
            newer_url=f"../{newer['date']}/" if newer else None,
            all_editions=all_editions_desc,
            site_root="../../",
        )
        (out_dir / "index.html").write_text(html, encoding="utf-8")

    # Latest edition also served at docs/index.html (site root)
    latest = editions[-1]
    older = editions[-2] if len(editions) > 1 else None
    all_editions_root = [dict(o, url=o["url"].replace("../editions/", "editions/")) for o in all_editions_desc]
    html_root = template.render(
        edition=latest,
        region_order=REGION_ORDER,
        older_url=f"editions/{older['date']}/" if older else None,
        newer_url=None,
        all_editions=all_editions_root,
        site_root="./",
    )
    (DOCS_DIR / "index.html").write_text(html_root, encoding="utf-8")

    # RSS feed — five-line digest per day, same content model as the
    # original site's feed.xml (title + concatenated top-five sentences)
    fg = FeedGenerator()
    fg.title(SITE_TITLE)
    fg.link(href=site_url, rel="alternate")
    fg.link(href=site_url.rstrip("/") + "/feed.xml", rel="self")
    fg.description("Daily humanitarian media-monitoring brief.")
    fg.language("en")

    # feedgen's add_entry() prepends by default, so add oldest-to-newest
    # to end up with the newest edition first in the published feed.
    for edition in editions[-30:]:
        fe = fg.add_entry()
        fe.title(f"{SITE_TITLE} — {edition['date']}")
        page_url = site_url.rstrip("/") + f"/editions/{edition['date']}/"
        fe.link(href=page_url)
        fe.guid(page_url, permalink=True)
        digest = " ".join(item["text"] for item in sorted(edition["top_five"], key=lambda i: i["rank"]))
        fe.description(digest)
        pub_dt = datetime.strptime(edition["date"], "%Y-%m-%d")
        fe.pubDate(pub_dt.strftime("%a, %d %b %Y 06:15:00 GMT"))

    fg.rss_file(str(DOCS_DIR / "feed.xml"))

    print(f"Rendered {len(editions)} edition(s) to {DOCS_DIR}")


if __name__ == "__main__":
    main()
