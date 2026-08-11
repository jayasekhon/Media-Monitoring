# The Daily Belle (self-hosted, rule-based edition)

A daily humanitarian media-monitoring briefing, styled after
[thedailybelle.org](https://www.thedailybelle.org/), generated with
**free tools only and no AI/LLM involved anywhere in the pipeline**:

- **GitHub Actions** triggers a build every day before 9am CET, using
  free Actions minutes.
- A **Python script** fetches free, keyless public sources — Google News
  RSS (queried per humanitarian theme) and the ReliefWeb public API —
  then classifies, scores, deduplicates, and extracts figures using
  plain keyword rules and string similarity. No model calls.
- **GitHub Pages** hosts the result, rebuilt automatically on every new
  edition.

This is a genuine tradeoff, not a free lunch — see
[`prompts/EDITORIAL_POLICY.md`](prompts/EDITORIAL_POLICY.md) for exactly
what got translated from the original analyst prompt into code, and what
a rule-based pipeline structurally cannot do that a human analyst or an
LLM could (real judgement, real writing, real reasoning about
escalation). Read that file before relying on this for anything
consequential.

## How it fits together

```
GitHub Actions (daily cron, 06:00 UTC)
   │
   ├─ scripts/build_edition.py
   │     ├─ fetch.py        → Google News RSS (per theme) + ReliefWeb API
   │     ├─ analyze.py      → region classification, keyword scoring,
   │     │                     figure extraction, similarity dedup
   │     └─ summarize.py    → extractive body text, templated risk text
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
scripts/config.py              Themes, regions, scoring keywords, source hierarchy — the "policy"
scripts/fetch.py                Pulls Google News RSS + ReliefWeb, no key required
scripts/analyze.py              Region classification, scoring, dedup, figure extraction
scripts/summarize.py            Extractive body text + templated escalation-risk phrasing
scripts/build_edition.py        Orchestrates the above into data/editions/YYYY-MM-DD.json
scripts/render_site.py          Builds docs/ (the live site) from all editions
scripts/validate_edition.py     Standalone schema check for one file
schema/edition.schema.json      The JSON contract between generation and rendering
templates/edition.html.jinja    Page layout
static/style.css                Visual design (masthead, colors, significance scale)
.github/workflows/daily.yml     Fetch → build → commit → render → deploy, all in one workflow
prompts/EDITORIAL_POLICY.md     Maps the original analyst prompt onto the code, and lists limitations
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
6. Trigger the workflow manually once (Actions tab → "Daily Belle — build
   and publish" → Run workflow) to test the real fetch-and-build pipeline
   end to end before waiting for the schedule.
7. Once confirmed, delete the two sample files
   (`data/editions/2026-08-09.json` and `2026-08-10.json`) — they're
   hand-authored examples for testing the layout, not real briefings.

## Local development

```bash
pip install -r requirements.txt

# Build today's edition from live sources
python scripts/build_edition.py

# Build a specific date (useful for testing)
python scripts/build_edition.py --date 2026-08-11

# Validate a file against the schema
python scripts/validate_edition.py data/editions/2026-08-11.json

# Render the site locally
python scripts/render_site.py
open docs/index.html
```

## Tuning behaviour

Almost everything worth adjusting lives in `scripts/config.py`:
themes searched, which countries map to which region, keyword→score
weights, the priority-crisis list, the outlet hierarchy, and the
blocklist. Change a value there before touching the pipeline logic
itself — it's designed so most tuning never requires editing
`fetch.py`/`analyze.py`/`summarize.py` directly.

## Known limitations (read this before trusting the output)

- **No real analytical judgement** — severity is keyword-counting, not
  understanding. See `prompts/EDITORIAL_POLICY.md` for the full list.
- **Google News RSS has no official uptime guarantee** and occasionally
  blocks automated traffic; the pipeline degrades gracefully (skips the
  failed theme, logs a warning) rather than crashing, but a bad day of
  fetching means a thin edition, not a failed build.
- **ReliefWeb's API** is stable and purpose-built for this use case —
  lean on it more (raise its query count, lower Google News reliance) if
  Google News proves unreliable in practice.
- Escalation-risk "confidence" is source-count, not real confidence.

## Design notes

Palette and typography were sampled directly from reference screenshots
of the original site: cream background (`#f1ecdf`), maroon/severity-red
accents, a four-tier significance scale (Watch/Serious/Severe/Critical →
gold/orange/maroon/black), and a dark "Early Warning Desk" panel for
escalation risks. See `static/style.css` for the full palette as CSS
variables.
