# OCHA Media Monitoring (self-hosted edition)

A daily humanitarian media-monitoring briefing, styled after
[thedailybelle.org](https://www.thedailybelle.org/), generated with
**free tools only**:

- **GitHub Actions** triggers a build every day before 9am CET, using
  free Actions minutes.
- A **Python script** fetches free, keyless sources — Google News RSS
  (queried per humanitarian theme) plus direct RSS feeds from BBC, Al
  Jazeera English, The Guardian, and France 24 English, since
  Google News' aggregation alone can miss real stories a direct outlet
  feed catches immediately — then classifies by region and does a coarse
  deduplication pass, all deterministic, no model involved. (ReliefWeb's
  API was dropped — its v1 endpoint returns 410 Gone as of this writing;
  see `scripts/fetch.py`'s module docstring.)
- **Gemini** (Google AI Studio's free API tier — needs a free API key,
  see `docs_GEMINI_SETUP.md`) then does the actual analytical work:
  scoring significance, writing original 40-120 word summaries, and
  reasoning about 72-hour escalation risk, region by region. If a call
  fails or the key's unavailable, that region falls back automatically
  to rule-based keyword scoring and extractive summarisation instead —
  the run never breaks because one model call did.
- **GitHub Pages** hosts the result, rebuilt automatically on every new
  edition.

**Note on provider history:** this originally ran on GitHub Models,
which was fully retired on July 30, 2026. If you have an older copy of
this repo referencing `GITHUB_TOKEN`/`permissions: models: read` for the
LLM layer, that no longer works — see `docs_GEMINI_SETUP.md` for the
current setup. This kind of provider churn is apparently just a fact of
life in this space; the pipeline's design (rule-based fallback on any
LLM failure) exists partly because of it.

See [`prompts/EDITORIAL_POLICY.md`](prompts/EDITORIAL_POLICY.md) for
exactly how the original analyst prompt maps onto both the LLM path and
the rule-based fallback, and what the fallback path genuinely can't do
that the LLM path can. Every edition's `source_notes.limitations`
records which path actually ran for each region that day — check it
before trusting the output for anything consequential.

## How it fits together

```
GitHub Actions (daily cron, 06:00 UTC)
   │
   ├─ scripts/build_edition.py
   │     ├─ fetch.py         → Google News RSS (per theme) + direct outlet RSS
   │     ├─ analyze.py       → region classification, coarse similarity dedup
   │     ├─ llm_pipeline.py  → Gemini: scoring, writing, risk reasoning
   │     │                      (per region + one cross-region synthesis call)
   │     └─ [fallback] summarize.py → rule-based scoring/writing if the
   │                                    LLM call for a region/synthesis fails
   │
   ├─ commits data/editions/YYYY-MM-DD.json
   │
   ├─ scripts/render_site.py
   │     └─ builds docs/ from every file in data/editions/
   │
   └─ publishes docs/ to GitHub Pages
```

## Repo layout

```
scripts/config.py              Themes, regions, scoring keywords, source hierarchy — the rule-based "policy"
scripts/fetch.py                Pulls Google News RSS + direct outlet RSS (BBC, Al Jazeera, Guardian, France24) + article-excerpt enrichment, no key required
scripts/analyze.py              Region classification, coarse dedup, figure extraction (rule-based, always runs)
scripts/llm_client.py           Thin Gemini API client — auth, retries, JSON extraction
scripts/llm_pipeline.py         Per-region + synthesis prompts and calls; the LLM judgment layer
scripts/summarize.py            Extractive body text + templated risk phrasing — the rule-based fallback
scripts/build_edition.py        Orchestrates all of the above into data/editions/YYYY-MM-DD.json
scripts/render_site.py          Builds docs/ (the live site) from all editions
scripts/validate_edition.py     Standalone schema check for one file
schema/edition.schema.json      The JSON contract between generation and rendering
templates/edition.html.jinja    Page layout
static/style.css                Visual design (masthead, colors, significance scale)
.github/workflows/daily.yml     Fetch → build (LLM + fallback) → commit → render → deploy, all in one workflow
prompts/EDITORIAL_POLICY.md     Maps the original analyst prompt onto both paths, and lists fallback limitations
docs_GEMINI_SETUP.md            How the LLM layer is wired in, getting a free API key, what to watch for
build_config.json               Your live site URL (used for RSS + canonical links)
data/editions/                  One JSON file per day
```

## Setup

1. Create a new GitHub repo and push this folder to it.
2. In **Settings → Pages**, set Source to **GitHub Actions**.
3. In **Settings → Actions → General → Workflow permissions**, select
   **"Read and write permissions"** — the daily workflow needs to commit
   the new edition file back to the repo.
4. Edit `build_config.json` — set `site_url` to your real Pages URL
   (`https://YOUR-USERNAME.github.io/YOUR-REPO/`).
5. Push. The two sample editions in `data/editions/` will build and
   deploy immediately, so you can confirm the site looks right.
6. Get a free Gemini API key and add it as a `GEMINI_API_KEY` repo
   secret — full steps in `docs_GEMINI_SETUP.md`. Without this, the
   pipeline still runs fine, just rule-based only.
7. Trigger the workflow manually once (Actions tab → "Daily Belle — build
   and publish" → Run workflow) to test the real fetch-and-build pipeline
   end to end before waiting for the schedule.
8. Once confirmed, delete the two sample files
   (`data/editions/2026-08-09.json` and `2026-08-10.json`) — they're
   hand-authored examples for testing the layout, not real briefings.

## Local development

```bash
pip install -r requirements.txt

# Build today's edition (LLM path if GEMINI_API_KEY is set locally, else rule-based)
python scripts/build_edition.py

# Force pure rule-based generation, e.g. to compare output quality
python scripts/build_edition.py --no-llm

# Build a specific date
python scripts/build_edition.py --date 2026-08-11

# Validate a file against the schema
python scripts/validate_edition.py data/editions/2026-08-11.json

# Render the site locally
python scripts/render_site.py
open docs/index.html
```

## Tuning behaviour

- **Analytical rules / prompt:** `scripts/llm_pipeline.py`'s
  `SYSTEM_POLICY` (LLM path) and `scripts/config.py` (rule-based
  fallback: themes, regions, keyword weights, priority-crisis list,
  outlet hierarchy, blocklist).
- **Model choice / call behaviour:** `scripts/llm_client.py`
  (`DEFAULT_MODEL`, retry/timeout settings, call spacing in
  `build_edition.py`'s `LLM_CALL_SPACING_SECONDS`).
- **Candidate volume sent to the LLM per region:**
  `config.LLM_CANDIDATE_POOL_SIZE`.

Change values in these files before touching orchestration logic in
`build_edition.py` — it's designed so most tuning never requires editing
the orchestration itself.

## Known limitations (read this before trusting the output)

- **The rule-based fallback path** (used when Gemini is unavailable or a
  call fails) has no real analytical judgement — severity is
  keyword-counting, not understanding. Full list in
  `prompts/EDITORIAL_POLICY.md`.
- **Gemini's free tier has daily/per-minute rate limits** that do change
  over time — see `docs_GEMINI_SETUP.md` for current guidance and where
  to check Google's own docs. This whole space moves fast: GitHub Models
  existed and seemed stable, then was fully retired within about a year.
  Don't assume today's numbers (or even today's provider) will still be
  accurate indefinitely.
- **Google News RSS has no official uptime guarantee** and occasionally
  blocks automated traffic; the pipeline degrades gracefully (skips the
  failed theme, logs a warning) rather than crashing.
- Always check `source_notes.limitations` on a given edition — it's an
  honest, per-run report of exactly what happened, not a static
  disclaimer.

## Design notes

Palette and typography were sampled directly from reference screenshots
of the original site: cream background (`#f1ecdf`), maroon/severity-red
accents, a four-tier significance scale (Watch/Serious/Severe/Critical →
gold/orange/maroon/black), and a dark "Early Warning Desk" panel for
escalation risks. See `static/style.css` for the full palette as CSS
variables.
