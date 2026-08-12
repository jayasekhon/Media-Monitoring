"""
fetch.py — pulls raw material from free, keyless public sources.

News outlets only, via Google News RSS queried per theme (scopes results
to the crisis topics we care about, and aggregates the outlet hierarchy
from the original prompt without needing 13 separate feed parsers).

ReliefWeb's public API was dropped from this pipeline — as of this
writing, api.reliefweb.int/v1 returns 410 Gone on every query (the v1
endpoint appears to have been retired). If ReliefWeb publishes a current
working endpoint later, re-adding a fetch_reliefweb() function here and
wiring it back into fetch_all() is straightforward — the rest of the
pipeline (dedup, scoring, region classification) doesn't care which
source an item came from.

Network calls are wrapped so a single failing theme doesn't take down
the whole run.

IMPORTANT — Google News RSS quirk: its <description> field is NOT a real
article summary. It's almost always just the headline again, wrapped in
decorative HTML, with the source name tacked on the end (something like
"<a href=...>Headline text</a>&nbsp;&nbsp;<font>Source Name</font>").
Stripping the HTML tags alone leaves that noise behind as if it were real
body text — which is what was causing report bodies to read like the
headline repeated twice followed by the outlet's own name, and made
nearly every item short enough to trip the "thin description" filler.
_clean_google_description() below detects and removes that pattern so a
Google News item with no real summary is treated as headline-only (empty
description) rather than as if it had (fake) body content.
"""
import time
import urllib.parse
import re
import html
from difflib import SequenceMatcher
from datetime import datetime, timezone

import requests
import feedparser

from config import THEMES, MONITORING_WINDOW_HOURS

USER_AGENT = "DailyBelleBot/1.0 (+https://github.com/; free, self-hosted humanitarian briefing)"
REQUEST_TIMEOUT = 15


def _strip_html(text: str) -> str:
    text = re.sub(r"<[^<]+?>", " ", text or "")
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _clean_google_description(headline: str, source_name: str, raw_summary: str) -> str:
    """Strip Google News RSS's headline-plus-source noise out of the
    description field, returning "" when there's no real summary content
    left (the common case) rather than a near-duplicate of the headline.
    """
    text = _strip_html(raw_summary)
    if not text:
        return ""

    # Drop a trailing "... Source Name" if it matches the byline we
    # already parsed off the title.
    if source_name and source_name != "Google News" and text.endswith(source_name):
        text = text[: -len(source_name)].strip(" -\u2013\u2014")

    if not text:
        return ""

    # If what's left is essentially just the headline again (Google News'
    # normal case), treat it as no real summary rather than keeping a
    # duplicate. Word-overlap similarity, not exact match, since minor
    # punctuation/casing differences are common.
    similarity = SequenceMatcher(None, text.lower(), headline.lower()).ratio()
    if similarity > 0.75:
        return ""

    return text


# ---------------------------------------------------------------------------
# Direct outlet RSS feeds — closes a real gap the Google-News-only version
# of this pipeline had: the original prompt calls for reviewing specific
# named outlets (Reuters, AFP, BBC, Al Jazeera...) directly, not just
# hoping Google News' aggregation happens to surface them. Google News
# search results can miss stories that a direct outlet feed catches
# immediately — e.g. Typhoon Dolphin coverage was absent from a
# Google-News-only run despite being a major, clearly-relevant story.
#
# Scoped to outlets with stable, reliable, genuinely public RSS feeds —
# not every outlet in SOURCE_HIERARCHY has one (Reuters and AFP notably
# don't run public RSS anymore). Add more here following the same
# (name, feed_url) pattern if you find other reliable ones.
# ---------------------------------------------------------------------------
OUTLET_RSS_FEEDS = [
    ("BBC", "https://feeds.bbci.co.uk/news/world/rss.xml"),
    ("Al Jazeera English", "https://www.aljazeera.com/xml/rss/all.xml"),
    ("The Guardian", "https://www.theguardian.com/world/rss"),
    ("France 24 English", "https://www.france24.com/en/rss"),
]


# ---------------------------------------------------------------------------
# Google-News-as-pseudo-RSS for outlets with no public RSS feed of their
# own (Reuters, AFP, AP all fall in this bucket) — uses Google's
# `allinurl:` search operator to scope results to one outlet's domain,
# giving an outlet-specific feed via Google News' search rather than a
# real RSS endpoint. Same Google News RSS response shape as
# fetch_google_news(), so it shares that parsing/cleanup logic; only the
# query construction differs.
#
# Yield varies by outlet and hasn't been verified live for all three:
# AP's public-facing apnews.com is a strong, mostly-open news portal, so
# this worked well there. AFP's afp.com is more corporate-facing with a
# lot of content only partially available without a subscription, so
# afp.com may yield noticeably fewer usable results than the other two —
# worth checking real output rather than assuming parity.
# ---------------------------------------------------------------------------
GOOGLE_NEWS_DOMAIN_SOURCES = [
    ("Associated Press", "apnews.com"),
    ("Reuters", "reuters.com"),
    ("AFP", "afp.com"),
]


def _parse_google_news_source(title: str):
    """Google News RSS titles are usually 'Headline - Source Name'."""
    if " - " in title:
        headline, source = title.rsplit(" - ", 1)
        return headline.strip(), source.strip()
    return title.strip(), "Google News"


def _fetch_google_news_query(query: str, matched_theme: str, origin: str,
                              window_hours: int = MONITORING_WINDOW_HOURS,
                              force_source_name: str = None):
    """Shared fetch+parse logic for any Google News RSS search query —
    used by both fetch_google_news() (theme search) and
    fetch_google_news_domain() (domain-scoped search). `force_source_name`
    overrides whatever Google's title-suffix parsing produces, for
    domain-scoped queries where we already know the outlet and want a
    consistent name rather than however Google happens to label it.
    """
    url = f"https://news.google.com/rss/search?q={urllib.parse.quote(query)}&hl=en-US&gl=US&ceid=US:en"
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
            if force_source_name:
                source_name = force_source_name
            description = _clean_google_description(headline, source_name, entry.get("summary", ""))
            items.append({
                "title": headline,
                "description": description,
                "link": entry.get("link", ""),
                "source": source_name,
                "published": datetime.fromtimestamp(published_ts, tz=timezone.utc).isoformat()
                             if published_ts else None,
                "matched_theme": matched_theme,
                "origin": origin,
            })
    except (requests.RequestException, Exception) as e:  # noqa: BLE001
        print(f"::warning::Google News fetch failed for query '{query}': {e}")
    return items


def fetch_google_news(theme: str, window_hours: int = MONITORING_WINDOW_HOURS):
    """Fetch Google News RSS results for one theme, last `window_hours`."""
    return _fetch_google_news_query(f"{theme} when:1d", theme, "google_news", window_hours)


def fetch_google_news_domain(source_name: str, domain: str, window_hours: int = MONITORING_WINDOW_HOURS):
    """Fetch Google News RSS results scoped to one outlet's domain via
    the `allinurl:` operator — see GOOGLE_NEWS_DOMAIN_SOURCES above.
    """
    matched_theme = f"__domain_via_google_news__{source_name}"
    return _fetch_google_news_query(
        f"when:24h allinurl:{domain}", matched_theme, "google_news_domain",
        window_hours, force_source_name=source_name,
    )


def fetch_outlet_rss(source_name: str, feed_url: str, window_hours: int = MONITORING_WINDOW_HOURS):
    """Fetch a general outlet RSS feed directly (BBC, Al Jazeera, etc.) —
    not theme-scoped like Google News, so this pulls in everything the
    outlet published in the window, humanitarian-relevant or not. Real
    outlet RSS descriptions are genuine article summaries, not the
    headline+source noise Google News produces, so no cleanup step is
    needed here beyond stripping HTML tags.

    Because these feeds aren't pre-scoped to a theme, expect most items
    to get dropped downstream by the relevance floor
    (MIN_KEYWORD_HITS_FOR_INCLUSION) — that's expected and correct, not
    a sign anything's wrong. `matched_theme` here is a synthetic label
    used only to look up a sensible default region if no country name in
    the text overrides it (see analyze.classify_region).
    """
    items = []
    try:
        resp = requests.get(feed_url, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        parsed = feedparser.parse(resp.content)
        cutoff = time.time() - window_hours * 3600
        for entry in parsed.entries:
            published_struct = entry.get("published_parsed") or entry.get("updated_parsed")
            published_ts = time.mktime(published_struct) if published_struct else None
            if published_ts is not None and published_ts < cutoff:
                continue
            title = entry.get("title", "").strip()
            if not title:
                continue
            description = _strip_html(entry.get("summary", ""))
            items.append({
                "title": title,
                "description": description,
                "link": entry.get("link", ""),
                "source": source_name,
                "published": datetime.fromtimestamp(published_ts, tz=timezone.utc).isoformat()
                             if published_ts else None,
                "matched_theme": f"__outlet_rss__{source_name}",
                "origin": "outlet_rss",
            })
    except (requests.RequestException, Exception) as e:  # noqa: BLE001
        print(f"::warning::{source_name} RSS fetch failed: {e}")
    return items


def fetch_all():
    """Fetch everything: Google News per theme + direct outlet RSS feeds
    + Google-News-domain-scoped feeds for outlets with no real RSS.

    Returns (items, theme_region_map) where theme_region_map lets the
    caller look up each item's default region via its matched_theme.
    """
    all_items = []
    theme_region_map = {theme: region for theme, region in THEMES}

    for theme, _region in THEMES:
        all_items.extend(fetch_google_news(theme))

    for source_name, feed_url in OUTLET_RSS_FEEDS:
        all_items.extend(fetch_outlet_rss(source_name, feed_url))
        theme_region_map.setdefault(f"__outlet_rss__{source_name}", "Global / Cross-Cutting")

    for source_name, domain in GOOGLE_NEWS_DOMAIN_SOURCES:
        all_items.extend(fetch_google_news_domain(source_name, domain))
        theme_region_map.setdefault(f"__domain_via_google_news__{source_name}", "Global / Cross-Cutting")

    return all_items, theme_region_map


# ---------------------------------------------------------------------------
# Article-excerpt enrichment — only called for the ~20-30 items that survive
# filtering, scoring and dedup and actually make it into the published
# edition, not for every raw candidate (fetching full articles for every
# theme's search results would be slow and mostly wasted work).
# ---------------------------------------------------------------------------
ARTICLE_FETCH_TIMEOUT = 10
ARTICLE_EXCERPT_MAX_CHARS = 700


def fetch_article_excerpt(url: str) -> tuple[str, str]:
    """Best-effort fetch of the actual article page and extraction of its
    opening text, for items whose RSS description had no real content
    (the common case for Google News — see module docstring).

    Returns (excerpt_text, resolved_url). `resolved_url` is the URL we
    actually landed on after following redirects — notably, this
    resolves Google News' fragile, expiring redirect-token links
    (news.google.com/rss/articles/CBMi...) to the real, stable publisher
    URL, which is worth capturing even when excerpt extraction itself
    comes up empty. Direct outlet-RSS links (BBC, Al Jazeera, etc.) are
    already stable and typically won't change.

    This follows the item's own link, the same as any RSS reader or the
    person clicking through themselves would — it does not bypass
    paywalls or authentication. Returns ("", original_url) on any
    failure (never raises): link doesn't resolve, publisher blocks
    automated requests, it's a Google redirect using a JS-based
    mechanism a plain HTTP fetch can't follow, or trafilatura can't find
    a clean article body on the page.
    """
    if not url:
        return "", url
    try:
        import trafilatura
    except ImportError:
        return "", url
    try:
        resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=ARTICLE_FETCH_TIMEOUT, allow_redirects=True)
        resolved_url = resp.url or url
        if resp.status_code != 200 or not resp.text:
            return "", resolved_url
        text = trafilatura.extract(resp.text, favor_precision=True) or ""
        text = re.sub(r"\s+", " ", text).strip()
        return text[:ARTICLE_EXCERPT_MAX_CHARS], resolved_url
    except Exception:  # noqa: BLE001 — best-effort, must never break the run
        return "", url


def enrich_items_with_article_text(items: list[dict], max_workers: int = 6) -> tuple[int, int]:
    """Mutates `items` in place: for any item whose description is empty
    or very short, tries to replace it with a real excerpt fetched from
    the article itself, AND updates `link` to the resolved URL whenever
    the fetch actually reached the page (even if excerpt extraction
    found nothing usable — a stable direct link is valuable on its own,
    independent of whether we got clean body text from it). Runs
    fetches concurrently since this is I/O-bound and each request can
    take several seconds.

    Returns (enriched_count, attempted_count) for limitations reporting.
    """
    to_enrich = [it for it in items if len(_strip_html(it.get("description", "")).split()) < 15 and it.get("link")]
    if not to_enrich:
        return 0, 0

    from concurrent.futures import ThreadPoolExecutor, as_completed

    enriched_count = 0
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        future_to_item = {pool.submit(fetch_article_excerpt, it["link"]): it for it in to_enrich}
        for future in as_completed(future_to_item):
            it = future_to_item[future]
            try:
                excerpt, resolved_url = future.result()
            except Exception:  # noqa: BLE001
                excerpt, resolved_url = "", it["link"]
            if excerpt:
                it["description"] = excerpt
                enriched_count += 1
            if resolved_url and resolved_url != it["link"]:
                it["link"] = resolved_url

    return enriched_count, len(to_enrich)


def resolve_published_source_links(sections: dict, escalation_risks: list, max_workers: int = 6) -> tuple[int, int]:
    """Final link-resolution pass over every source URL that will
    actually appear in the published edition — report sources AND
    escalation-risk sources, including ones added by the corroboration
    check (which runs after enrich_items_with_article_text and so never
    got a resolution attempt before).

    This is deliberately decoupled from enrich_items_with_article_text():
    that function only resolves links for items whose description looked
    "thin" — a proxy for "needs a content summary", not "needs a stable
    URL". Those aren't the same thing. A Google News "full coverage"
    description (several headlines concatenated) can total 15+ words
    while still being pure noise, dodging the thin-description trigger
    entirely and leaving that item's link unresolved even though it was
    never a good link to begin with. Every source actually shown on the
    site needs a working link — so this pass targets that directly:
    every unique news.google.com URL among published sources, once,
    regardless of whether an earlier pass already tried it.

    Only news.google.com URLs are touched — direct outlet-RSS links
    (BBC, Al Jazeera, Guardian, France24) are already stable and this
    would just be wasted requests.

    Mutates the `url` field of every matching source dict in place
    (multiple sources can share the same underlying URL — e.g. a
    corroborated source pointing at the same article a differently-
    hyphenated duplicate already cited — all get updated together).

    Returns (resolved_count, attempted_count) for limitations reporting.
    """
    # Map each unique URL needing resolution to every source dict that
    # references it, so one fetch can update every occurrence.
    url_to_source_dicts: dict[str, list[dict]] = {}
    for reports in sections.values():
        for rep in reports:
            for s in rep.get("sources", []):
                url = s.get("url", "")
                if url and "news.google.com" in url:
                    url_to_source_dicts.setdefault(url, []).append(s)
    for risk in escalation_risks:
        for s in risk.get("sources", []):
            url = s.get("url", "")
            if url and "news.google.com" in url:
                url_to_source_dicts.setdefault(url, []).append(s)

    if not url_to_source_dicts:
        return 0, 0

    from concurrent.futures import ThreadPoolExecutor, as_completed

    resolved_count = 0
    urls = list(url_to_source_dicts.keys())
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        future_to_url = {pool.submit(fetch_article_excerpt, url): url for url in urls}
        for future in as_completed(future_to_url):
            original_url = future_to_url[future]
            try:
                _excerpt, resolved_url = future.result()
            except Exception:  # noqa: BLE001
                resolved_url = original_url
            if resolved_url and resolved_url != original_url:
                for s in url_to_source_dicts[original_url]:
                    s["url"] = resolved_url
                resolved_count += 1

    return resolved_count, len(urls)
