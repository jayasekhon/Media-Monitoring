"""
fetch.py — pulls raw material from free, keyless public sources.

Two sources, deliberately:
  - Google News RSS, queried per theme (already scopes results to the
    crisis topics we care about, and aggregates the outlet hierarchy from
    the original prompt without needing 13 separate feed parsers).
  - ReliefWeb's public API (api.reliefweb.int), which is purpose-built
    humanitarian reporting and needs no key.

Both are free and require no authentication. Network calls are wrapped so
a single failing theme/query doesn't take down the whole run.
"""
import time
import urllib.parse
import re
from datetime import datetime, timezone

import requests
import feedparser

from config import THEMES, RELIEFWEB_QUERIES, MONITORING_WINDOW_HOURS

USER_AGENT = "DailyBelleBot/1.0 (+https://github.com/; free, self-hosted humanitarian briefing)"
REQUEST_TIMEOUT = 15


def _strip_html(text: str) -> str:
    text = re.sub(r"<[^<]+?>", " ", text or "")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _parse_google_news_source(title: str):
    """Google News RSS titles are usually 'Headline - Source Name'."""
    if " - " in title:
        headline, source = title.rsplit(" - ", 1)
        return headline.strip(), source.strip()
    return title.strip(), "Google News"


def fetch_google_news(theme: str, window_hours: int = MONITORING_WINDOW_HOURS):
    """Fetch Google News RSS results for one theme, last `window_hours`."""
    query = urllib.parse.quote(f"{theme} when:1d")
    url = f"https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en"
    items = []
    try:
        resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        parsed = feedparser.parse(resp.content)
        cutoff = time.time() - window_hours * 3600
        for entry in parsed.entries:
            published_struct = entry.get("published_parsed")
            published_ts = time.mktime(published_struct) if published_struct else None
            if published_ts is not None and published_ts < cutoff:
                continue
            headline, source_name = _parse_google_news_source(entry.get("title", ""))
            description = _strip_html(entry.get("summary", ""))
            items.append({
                "title": headline,
                "description": description,
                "link": entry.get("link", ""),
                "source": source_name,
                "published": datetime.fromtimestamp(published_ts, tz=timezone.utc).isoformat()
                             if published_ts else None,
                "matched_theme": theme,
                "origin": "google_news",
            })
    except (requests.RequestException, Exception) as e:  # noqa: BLE001
        print(f"::warning::Google News fetch failed for theme '{theme}': {e}")
    return items


def fetch_reliefweb(query: str, window_hours: int = MONITORING_WINDOW_HOURS):
    """Fetch ReliefWeb reports mentioning `query`, last `window_hours`."""
    url = "https://api.reliefweb.int/v1/reports"
    params = {
        "appname": "dailybelle-selfhosted",
        "query[value]": query,
        "query[operator]": "AND",
        "sort[]": "date:desc",
        "limit": 8,
        "fields[include][]": ["title", "body-html", "date.created", "url", "source.name"],
    }
    items = []
    try:
        resp = requests.get(url, params=params, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        cutoff = datetime.now(timezone.utc).timestamp() - window_hours * 3600
        for entry in data.get("data", []):
            fields = entry.get("fields", {})
            created = fields.get("date", {}).get("created")
            created_ts = None
            if created:
                try:
                    created_ts = datetime.fromisoformat(created.replace("Z", "+00:00")).timestamp()
                except ValueError:
                    created_ts = None
            if created_ts is not None and created_ts < cutoff:
                continue
            source_names = [s.get("name", "ReliefWeb") for s in fields.get("source", [{}])] if fields.get("source") else ["ReliefWeb"]
            items.append({
                "title": fields.get("title", ""),
                "description": _strip_html(fields.get("body-html", ""))[:800],
                "link": fields.get("url", ""),
                "source": source_names[0] if source_names else "ReliefWeb",
                "published": created,
                "matched_theme": query,
                "origin": "reliefweb",
            })
    except (requests.RequestException, ValueError, Exception) as e:  # noqa: BLE001
        print(f"::warning::ReliefWeb fetch failed for query '{query}': {e}")
    return items


def fetch_all():
    """Fetch everything: Google News per theme + ReliefWeb per query.

    Returns (items, theme_region_map) where theme_region_map lets the
    caller look up each item's default region via its matched_theme.
    """
    all_items = []
    theme_region_map = {theme: region for theme, region in THEMES}

    for theme, _region in THEMES:
        all_items.extend(fetch_google_news(theme))

    for query in RELIEFWEB_QUERIES:
        all_items.extend(fetch_reliefweb(query))
        # ReliefWeb queries aren't in THEMES, so give them a sensible
        # default region lookup too (falls back to country detection anyway).
        theme_region_map.setdefault(query, "Global / Cross-Cutting")

    return all_items, theme_region_map
